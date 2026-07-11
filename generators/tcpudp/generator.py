"""
TCP/UDP Raw Traffic Generator
------------------------------
Sends raw TCP and UDP packets at a configurable rate.

Two size/timing modes (key feature for Wireshark Behavioral Fingerprinting analysis):

  NORMAL mode  -> fixed packet size (512B), fixed interval (100ms)
                  Creates a clear statistical fingerprint, easily detected.

  STEALTH mode -> random packet size (64-1400B), Poisson-distributed timing
                  Mimics real user traffic, statistically indistinguishable.

Independently of `mode`, a `pattern` controls the overall sending cadence
(temporal shape of the traffic, relevant for the Temporal Analysis task):

  constant       -> one packet per interval, interval derived from tcp_rate/udp_rate
  periodic_burst -> each protocol's effective rate becomes max(its own rate,
                     `burst_rate`) for `burst_duration` seconds every
                     `burst_interval` seconds, stateless/clock-derived (wall
                     time modulo burst_interval) exactly like the HTTP/2
                     generator's burst pattern - survives restarts/event-loop
                     delays without drifting, unlike a running-counter timer
  random         -> exponentially-distributed (Poisson) gaps between sends
  ramp           -> total packet rate increases linearly from `ramp_start_rate`
                     to `ramp_end_rate` (packets/sec, combined TCP+UDP) over
                     `ramp_duration` seconds, then holds at `ramp_end_rate`;
                     the combined rate is split into TCP/UDP using `tcp_ratio`,
                     same as the other patterns

Exposes a small REST API so the Traffic Controller can start/stop/reconfigure
this generator at runtime and read its live statistics (including TCP connect
latency, used as a signal for Adaptive Control).
"""

import os, time, socket, random, threading, traceback
from typing import Optional

import numpy as np
import requests as req_sync
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

app = FastAPI(
    title="MIC TCP/UDP Generator",
    description="Sends raw TCP/UDP traffic with configurable rate, packet size, protocol mix and "
                 "Normal vs. Stealth timing/size patterns (used for the Behavioral Fingerprinting analysis).",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TARGET_HOST = os.getenv("TARGET_HOST", "target-tcpudp")
TARGET_PORT = int(os.getenv("TARGET_PORT", "9999"))
METRICS_URL = os.getenv("METRICS_URL", "http://metrics:9090")
PORT        = int(os.getenv("PORT", "7004"))

state = {
    "running":      False,
    "mode":         "normal",   # "normal" | "stealth"
    "tcp_rate":     10,         # TCP packets/sec (normal mode)
    "udp_rate":     5,          # UDP packets/sec (normal mode)
    "tcp_packet_size": 512,     # bytes (normal mode, fixed) - TCP packets
    "udp_packet_size": 512,     # bytes (normal mode, fixed) - UDP packets
    "mean_interval":0.100,      # seconds (stealth mode Poisson mean)
    "min_size":     64,         # bytes (stealth mode)
    "max_size":     1400,       # bytes (stealth mode)
    "tcp_ratio":    60,         # % TCP, rest UDP
    "pattern":      "constant", # "constant" | "periodic_burst" | "random" | "ramp"
    "tcp_burst_rate": 0,        # TCP packets/sec during a burst window (periodic_burst); 0 = TCP never bursts
    "udp_burst_rate": 0,        # UDP packets/sec during a burst window (periodic_burst); 0 = UDP never bursts
    "burst_duration": 5.0,      # seconds: length of each burst window (periodic_burst)
    "burst_interval": 30.0,     # seconds: period between the start of consecutive burst windows (periodic_burst)
    "burst_size":     10,       # deprecated/unused by periodic_burst now; kept for API backward-compat
    "ramp_start_rate":  0,      # combined TCP+UDP packets/sec at the start of a 'ramp' pattern
    "ramp_end_rate":    50,     # combined TCP+UDP packets/sec at the end of a 'ramp' pattern
    "ramp_duration":    60,     # seconds: how long the linear ramp takes
    "packets_sent": 0,
    "bytes_sent":   0,
    "errors":       0,
    "rate_bps":     0,
    "latency_ms":   0,
    "fault_rate":         0.0,   # 0.0-1.0: probability that a send is simulated as a failure (no real send)
    "extra_latency_ms":   0,     # artificial extra delay injected before each send
}

_lock = threading.Lock()
# Set by /stop to wake the send loop immediately; cleared by /start so waits work normally.
_stop_requested = threading.Event()
_bytes_window: list[tuple[float, int]] = []
_latency_window: list[tuple[float, float]] = []

# Wall-clock timestamp at which the current 'ramp' pattern run began. See the
# HTTP/2 generator for the full rationale; same mechanism here.
_ramp_started_at: Optional[float] = None
_last_pattern: Optional[str] = None

# Next wall-clock time each protocol is due to send, for pattern='constant'/
# 'random'. TCP and UDP are paced fully independently here, each strictly at
# its own configured tcp_rate/udp_rate - unlike periodic_burst/ramp, which
# intentionally use tcp_ratio to split a single combined rate (see their
# docstrings), 'constant'/'random' must NOT pick a protocol via a tcp_ratio
# coin-flip: that decouples the chosen protocol's actual send frequency from
# its own tcp_rate/udp_rate value entirely (e.g. tcp_rate=5/udp_rate=150 would
# still send TCP 60% of the time by default tcp_ratio, producing a TCP:UDP
# packet ratio dominated by the fixed 60/40 split rather than the configured
# 5:150 rates).
_next_due: dict[str, float] = {"tcp": 0.0, "udp": 0.0}

# Ring buffer of recent metric snapshots (one per _metrics_loop() tick, ~5s apart),
# capped to the last 5 minutes. Exposed via /status so the dashboard can draw
# live rate/latency/error sparklines without polling a separate endpoint.
_HISTORY_MAXLEN = 60
_history: list[dict] = []


# ── Models (Swagger) ────────────────────────────────────────────────────────

class GeneratorConfig(BaseModel):
    """Configurable parameters. All fields optional; only provided keys are updated."""
    model_config = ConfigDict(extra="allow", json_schema_extra={
        "example": {"mode": "stealth", "pattern": "periodic_burst", "tcp_rate": 20, "udp_rate": 10,
                     "tcp_ratio": 60, "tcp_burst_rate": 200, "udp_burst_rate": 150,
                     "burst_duration": 4, "burst_interval": 15,
                     "fault_rate": 0.0, "extra_latency_ms": 0}
    })
    mode: Optional[str] = Field(
        None, description="Packet-size/timing-base mode: 'normal' (fixed size) or 'stealth' (random size)."
    )
    pattern: Optional[str] = Field(
        None,
        description="Overall sending cadence: 'constant' (one packet per interval derived from "
                     "tcp_rate/udp_rate), 'periodic_burst' (each protocol's effective rate becomes "
                     "max(its own rate, its own burst rate) for burst_duration seconds every "
                     "burst_interval seconds - a protocol at rate=0 stays silent even during a burst "
                     "window), 'random' (Poisson/exponentially-distributed gaps between sends), or "
                     "'ramp' (combined TCP+UDP packet rate increases linearly from `ramp_start_rate` "
                     "to `ramp_end_rate` over `ramp_duration` seconds, then holds at `ramp_end_rate`; "
                     "split into TCP/UDP using `tcp_ratio`)."
    )
    rate: Optional[float] = Field(
        None, ge=0,
        description="Combined TCP+UDP packets/sec shorthand: distributes into tcp_rate and udp_rate "
                     "using the current tcp_ratio (default 60/40). Equivalent to setting "
                     "tcp_rate=rate*tcp_ratio/100 and udp_rate=rate*(1-tcp_ratio/100). "
                     "Providing tcp_rate/udp_rate directly overrides this."
    )
    tcp_rate: Optional[float] = Field(None, ge=0, description="TCP packets/sec target (used by 'constant'/'random' patterns).")
    udp_rate: Optional[float] = Field(None, ge=0, description="UDP packets/sec target (used by 'constant'/'random' patterns).")
    tcp_packet_size: Optional[int] = Field(None, ge=0, description="Fixed TCP packet size (bytes) in 'normal' mode.")
    udp_packet_size: Optional[int] = Field(None, ge=0, description="Fixed UDP packet size (bytes) in 'normal' mode.")
    packet_size: Optional[int] = Field(
        None, ge=0,
        description="Deprecated shorthand: sets both tcp_packet_size and udp_packet_size to the "
                     "same value. Ignored if tcp_packet_size/udp_packet_size are also provided."
    )
    mean_interval: Optional[float] = None
    min_size: Optional[int] = Field(None, ge=0, description="Minimum packet size (bytes) in 'stealth' mode.")
    max_size: Optional[int] = Field(None, ge=0, description="Maximum packet size (bytes) in 'stealth' mode.")
    tcp_ratio: Optional[int] = Field(None, ge=0, le=100, description="Percentage of packets sent as TCP (rest UDP).")
    tcp_burst_rate: Optional[float] = Field(
        None, ge=0,
        description="TCP packets/sec (max of tcp_rate and this) during a burst window, when "
                     "pattern='periodic_burst'. 0 (the default) means TCP never bursts."
    )
    udp_burst_rate: Optional[float] = Field(
        None, ge=0,
        description="UDP packets/sec (max of udp_rate and this) during a burst window, when "
                     "pattern='periodic_burst'. 0 (the default) means UDP never bursts."
    )
    burst_rate: Optional[float] = Field(
        None, ge=0,
        description="Deprecated shorthand: sets both tcp_burst_rate and udp_burst_rate to the same "
                     "value. Ignored if tcp_burst_rate/udp_burst_rate are also provided."
    )
    burst_duration: Optional[float] = Field(
        None, ge=0,
        description="Length (seconds) of each burst window, when pattern='periodic_burst'."
    )
    burst_interval: Optional[float] = Field(
        None, ge=0,
        description="Period (seconds) between the start of consecutive burst windows, when "
                     "pattern='periodic_burst'."
    )
    burst_size: Optional[int] = Field(
        None, ge=1, le=200,
        description="Deprecated, no longer used by periodic_burst (which now uses burst_rate/"
                     "burst_duration/burst_interval, matching the other generators). Kept only "
                     "for API backward-compatibility."
    )
    ramp_start_rate: Optional[float] = Field(
        None, ge=0,
        description="Combined TCP+UDP packets/sec at the start of a linear ramp, when pattern='ramp'."
    )
    ramp_end_rate: Optional[float] = Field(
        None, ge=0,
        description="Combined TCP+UDP packets/sec at the end of a linear ramp, when pattern='ramp'. "
                     "Holds at this value once `ramp_duration` has elapsed."
    )
    ramp_duration: Optional[float] = Field(
        None, gt=0,
        description="Duration (seconds) over which the combined rate increases linearly from "
                     "`ramp_start_rate` to `ramp_end_rate`, when pattern='ramp'."
    )
    fault_rate: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Fraction of sends (0-1) deliberately treated as failures, without sending, "
                     "for resilience and failure-injection demos."
    )
    extra_latency_ms: Optional[float] = Field(
        None, ge=0,
        description="Artificial extra delay (ms) injected before every send, simulating "
                     "network congestion or an overloaded target."
    )


class StatusResponse(BaseModel):
    running: bool
    mode: str
    pattern: str
    tcp_rate: float
    udp_rate: float
    tcp_packet_size: int
    udp_packet_size: int
    mean_interval: float
    min_size: int
    max_size: int
    tcp_ratio: int
    tcp_burst_rate: float
    udp_burst_rate: float
    burst_duration: float
    burst_interval: float
    burst_size: int
    ramp_start_rate: float
    ramp_end_rate: float
    ramp_duration: float
    ramp_progress: float = Field(
        0.0, description="Fraction (0.0-1.0) of `ramp_duration` elapsed since the current "
                          "'ramp' pattern run started (pattern='ramp' only; 0.0 otherwise)."
    )
    packets_sent: int
    bytes_sent: int
    errors: int
    rate_bps: int
    latency_ms: float
    fault_rate: float
    extra_latency_ms: float
    history: list[dict] = Field(
        default_factory=list,
        description="Recent metric snapshots (~5s apart, up to 5 minutes), each "
                     "{ts, rate_bps, latency_ms, errors, packets_sent}. For live dashboard charts."
    )


class OkResponse(BaseModel):
    ok: bool = True
    mode: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normal_params() -> tuple[int, float, str]:
    """Fixed size and fixed interval; leaves a clear Wireshark fingerprint."""
    with _lock:
        tcp_size = state["tcp_packet_size"]
        udp_size = state["udp_packet_size"]
        tcp_rate = state["tcp_rate"]
        udp_rate = state["udp_rate"]
        tcp_pct  = state["tcp_ratio"]

    proto    = "tcp" if random.randint(1, 100) <= tcp_pct else "udp"
    size     = tcp_size if proto == "tcp" else udp_size
    rate     = tcp_rate if proto == "tcp" else udp_rate
    interval = 1.0 / rate if rate > 0 else 0.5
    return size, interval, proto


def _stealth_params() -> tuple[int, float, str]:
    """Random size and Poisson timing; mimics real user traffic patterns."""
    with _lock:
        mean     = state["mean_interval"]
        min_s    = state["min_size"]
        max_s    = state["max_size"]
        tcp_pct  = state["tcp_ratio"]

    size     = random.randint(min_s, max_s)
    # Exponential distribution = inter-arrival time of a Poisson process
    interval = np.random.exponential(mean)
    proto    = "tcp" if random.randint(1, 100) <= tcp_pct else "udp"
    return size, interval, proto


_consecutive_send_failures = 0
_FAILURE_LOG_EVERY_N = 20  # log the currently-resolved target IP every Nth
                           # consecutive failure, to catch a stale-DNS scenario


def _note_send_result(success: bool):
    """Tracks consecutive failures across both _send_tcp/_send_udp; every Nth
    consecutive failure, logs what TARGET_HOST currently resolves to. Neither
    socket.connect() nor socket.sendto() cache resolution themselves - each
    call re-resolves the hostname fresh - so this should always show a fresh
    IP once Docker recreates the target container; if it ever doesn't, this
    log line is what would catch that."""
    global _consecutive_send_failures
    if success:
        _consecutive_send_failures = 0
        return
    _consecutive_send_failures += 1
    if _consecutive_send_failures % _FAILURE_LOG_EVERY_N == 0:
        try:
            resolved_ip = socket.gethostbyname(TARGET_HOST)
        except Exception as exc:
            resolved_ip = f"<resolution failed: {exc!r}>"
        print(f"[gen-tcpudp] {_consecutive_send_failures} consecutive send failures; "
              f"{TARGET_HOST} currently resolves to {resolved_ip}", flush=True)


def _send_tcp(data: bytes, t0: float):
    """`t0` is the caller's start time (captured before any extra_latency_ms
    sleep), not a fresh one here, so elapsed_ms below reflects the full
    injected delay plus the real connect+send time, not just the latter."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect((TARGET_HOST, TARGET_PORT))
        s.sendall(data)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        with _lock:
            state["packets_sent"] += 1
            state["bytes_sent"]   += len(data)
        _bytes_window.append((time.time(), len(data)))
        _latency_window.append((time.time(), elapsed_ms))
        _note_send_result(True)
    except Exception:
        # Real failure (refused/timed out after up to 2s, see settimeout above) -
        # record how long it actually took, so latency_ms reflects genuine
        # degradation too, not just successful sends and injected faults.
        elapsed_ms = (time.perf_counter() - t0) * 1000
        with _lock:
            state["errors"] += 1
        _latency_window.append((time.time(), elapsed_ms))
        _note_send_result(False)
    finally:
        s.close()


def _send_udp(data: bytes, t0: float):
    """`t0` is the caller's start time (captured before any extra_latency_ms
    sleep), matching _send_tcp, so extra_latency_ms shows up in latency_ms
    for UDP too - previously a successful UDP send never touched
    _latency_window at all, so latency_ms only ever reflected TCP sends."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(data, (TARGET_HOST, TARGET_PORT))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        with _lock:
            state["packets_sent"] += 1
            state["bytes_sent"]   += len(data)
        _bytes_window.append((time.time(), len(data)))
        _latency_window.append((time.time(), elapsed_ms))
        _note_send_result(True)
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        with _lock:
            state["errors"] += 1
        _latency_window.append((time.time(), elapsed_ms))
        _note_send_result(False)
    finally:
        s.close()


# ── Pattern helpers (ramp progress tracking) ───────────────────────────────────

def _ramp_progress(pattern: str, ramp_duration: float) -> float:
    """Returns the fraction (0.0-1.0) of `ramp_duration` elapsed since the current
    'ramp' run started. See the HTTP/2 generator for the full rationale."""
    global _ramp_started_at
    if pattern != "ramp" or ramp_duration <= 0:
        return 0.0
    if _ramp_started_at is None:
        _ramp_started_at = time.time()
    elapsed = time.time() - _ramp_started_at
    return max(0.0, min(1.0, elapsed / ramp_duration))


_INDEPENDENT_SCHEDULE_PATTERNS = ("constant", "random", "periodic_burst")


def _note_pattern_transition(pattern: str):
    """Resets the ramp anchor whenever `pattern` transitions into 'ramp'; resets
    the independent TCP/UDP due-time schedule whenever `pattern` transitions
    into 'constant'/'random'/'periodic_burst' from something else, so a stale
    due-time left over from 'ramp' doesn't delay or burst the first packet."""
    global _last_pattern, _ramp_started_at
    if pattern == "ramp" and _last_pattern != "ramp":
        _ramp_started_at = time.time()
    elif pattern != "ramp":
        _ramp_started_at = None
    if pattern in _INDEPENDENT_SCHEDULE_PATTERNS and _last_pattern not in _INDEPENDENT_SCHEDULE_PATTERNS:
        _next_due["tcp"] = _next_due["udp"] = 0.0
    _last_pattern = pattern


def _is_burst_active(burst_duration: float, burst_interval: float) -> bool:
    """Returns True if the current moment falls inside a periodic_burst window.
    Bursts recur every `burst_interval` seconds and last `burst_duration`
    seconds, aligned to the wall clock (epoch time) - stateless and clock-
    derived, so the cadence is stable across restarts/event-loop delays and
    observable in Wireshark, exactly like the HTTP/2 generator's burst logic."""
    if burst_interval <= 0:
        return False
    return (time.time() % burst_interval) < burst_duration


def _ramp_effective_rate(ramp_start_rate: float, ramp_end_rate: float, progress: float) -> float:
    """Linearly interpolates between ramp_start_rate and ramp_end_rate. Used here as
    a single combined TCP+UDP packets/sec target; the per-packet protocol choice
    still comes from `tcp_ratio` inside _normal_params/_stealth_params, so the
    ramp only overrides the *interval* those helpers would otherwise compute from
    the fixed tcp_rate/udp_rate."""
    return ramp_start_rate + (ramp_end_rate - ramp_start_rate) * progress


# ── Traffic loop ──────────────────────────────────────────────────────────────

def _send_packet(proto: str, size: int, fault_rate: float, extra_latency: float):
    """Sends one packet of the given protocol (or simulates an injected fault),
    updating error/latency bookkeeping. Shared by the independent constant/
    random scheduler and the periodic_burst/ramp packet loop below."""
    t0 = time.perf_counter()

    if extra_latency > 0:
        time.sleep(extra_latency / 1000)

    if fault_rate > 0 and random.random() < fault_rate:
        # Injected fault: simulate a failed send without touching the socket.
        # Measured (not just the raw extra_latency config value) for
        # consistency with the other generators and with the real-failure
        # path in _send_tcp below.
        elapsed_ms = (time.perf_counter() - t0) * 1000
        with _lock:
            state["errors"] += 1
        _latency_window.append((time.time(), elapsed_ms))
    else:
        payload = os.urandom(size)
        if proto == "tcp":
            _send_tcp(payload, t0)
        else:
            _send_udp(payload, t0)


def _send_loop_iteration():
    """Runs exactly one iteration's worth of work (one scheduling decision and
    at most one packet send). Split out from _send_loop() so the outer loop
    can wrap a single call in try/except: every `continue` below became a
    `return` (equivalent - "skip to the next while-loop pass") so the two are
    behaviorally identical, but now any unexpected exception here (a bad
    config field, a stats/np call, anything other than the socket I/O already
    guarded inside _send_tcp/_send_udp/_send_packet) can be caught by the
    caller instead of silently killing this daemon thread forever."""
    with _lock:
        running         = state["running"]
        mode            = state["mode"]
        pattern         = state["pattern"]
        fault_rate      = state["fault_rate"]
        extra_latency   = state["extra_latency_ms"]
        tcp_burst_rate  = state["tcp_burst_rate"]
        udp_burst_rate  = state["udp_burst_rate"]
        burst_duration  = max(0.0, state["burst_duration"])
        burst_interval  = max(0.0, state["burst_interval"])
        ramp_start      = state["ramp_start_rate"]
        ramp_end        = state["ramp_end_rate"]
        ramp_duration   = state["ramp_duration"]
        tcp_rate        = state["tcp_rate"]
        udp_rate        = state["udp_rate"]
        tcp_packet_size = state["tcp_packet_size"]
        udp_packet_size = state["udp_packet_size"]
        min_size        = state["min_size"]
        max_size        = state["max_size"]

    _note_pattern_transition(pattern)

    if not running:
        _stop_requested.wait(timeout=0.1)
        return

    if pattern in _INDEPENDENT_SCHEDULE_PATTERNS:
        # TCP and UDP are paced fully independently here, each strictly at
        # its own tcp_rate/udp_rate (elevated to burst_rate during a burst
        # window, for periodic_burst) - see _next_due's docstring for why
        # this must not go through the tcp_ratio coin-flip that
        # _normal_params/_stealth_params use below for 'ramp'.
        now = time.time()
        if _next_due["tcp"] > now and _next_due["udp"] > now:
            wait = min(_next_due["tcp"], _next_due["udp"]) - now
            _stop_requested.wait(timeout=wait)
            return

        proto = "tcp" if _next_due["tcp"] <= _next_due["udp"] else "udp"
        rate  = tcp_rate if proto == "tcp" else udp_rate
        # A protocol with rate=0 (intentionally silent) or burst_rate=0
        # (never configured to burst - the default, so a protocol this
        # profile phase doesn't mention stays untouched) never gets
        # pulled up during a burst window. TCP and UDP have independent
        # burst rates (matching tcp_packet_size/udp_packet_size) since
        # profiles can burst only one protocol (tcpudp_heavy.yaml's
        # tcp_burst phase) or both at different rates (burst_mode.yaml).
        burst_rate = tcp_burst_rate if proto == "tcp" else udp_burst_rate
        if (pattern == "periodic_burst" and rate > 0 and burst_rate > 0
                and _is_burst_active(burst_duration, burst_interval)):
            rate = max(rate, burst_rate)
        size  = (
            (tcp_packet_size if proto == "tcp" else udp_packet_size) if mode == "normal"
            else random.randint(min_size, max_size)
        )

        interval = 1.0 / rate if rate > 0 else 1.0
        if pattern == "random":
            # Exponential inter-arrival time => Poisson process, same mean rate.
            interval = np.random.exponential(interval)
        _next_due[proto] = time.time() + interval

        _send_packet(proto, size, fault_rate, extra_latency)
        return

    # ramp: protocol choice here intentionally still comes from tcp_ratio,
    # splitting a single combined rate (see _ramp_effective_rate's
    # docstring) - constant/random/periodic_burst are handled above.
    progress = _ramp_progress(pattern, ramp_duration)
    effective_rate = _ramp_effective_rate(ramp_start, ramp_end, progress)
    if effective_rate <= 0:
        _stop_requested.wait(timeout=0.1)
        return
    ramp_interval = 1.0 / effective_rate

    with _lock:
        if not state["running"]:
            return

    size, _, proto = _normal_params() if mode == "normal" else _stealth_params()
    _send_packet(proto, size, fault_rate, extra_latency)
    _stop_requested.wait(timeout=ramp_interval)


def _send_loop():
    while True:
        try:
            _send_loop_iteration()
        except Exception as exc:
            # Without this, ANY unexpected exception here (a bad config
            # field, a stats/np call, anything other than the socket I/O
            # already guarded inside _send_tcp/_send_udp) would silently
            # kill this daemon thread forever: traffic would go quiet
            # permanently, with nothing in `docker logs` to explain why, and
            # no restart of the *target* container could ever bring it back
            # since the thread sending to it would simply no longer exist.
            print(f"[gen-tcpudp] _send_loop iteration crashed, continuing: {exc!r}", flush=True)
            traceback.print_exc()
            time.sleep(0.1)


def _metrics_loop():
    while True:
        time.sleep(5)
        now = time.time()
        with _lock:
            # list operations inside lock — prevents RuntimeError from concurrent .append()
            recent = [b for ts, b in _bytes_window if now - ts <= 10]
            _bytes_window[:] = [(ts, b) for ts, b in _bytes_window if now - ts <= 10]
            rate_bps = sum(recent) / 10 if recent else 0

            recent_lat = [l for ts, l in _latency_window if now - ts <= 10]
            _latency_window[:] = [(ts, l) for ts, l in _latency_window if now - ts <= 10]
            avg_latency = sum(recent_lat) / len(recent_lat) if recent_lat else 0

            state["rate_bps"]   = int(rate_bps)
            state["latency_ms"] = round(avg_latency, 2)
            _history.append({
                "ts":           now,
                "rate_bps":     state["rate_bps"],
                "latency_ms":   state["latency_ms"],
                "errors":       state["errors"],
                "packets_sent": state["packets_sent"],
            })
            del _history[:-_HISTORY_MAXLEN]
            payload = {
                "generator":    "gen-tcpudp",
                "running":      state["running"],
                "mode":         state["mode"],
                "packets_sent": state["packets_sent"],
                "bytes_sent":   state["bytes_sent"],
                "errors":       state["errors"],
                "rate_bps":     state["rate_bps"],
                "latency_ms":   state["latency_ms"],
            }

        try:
            req_sync.post(f"{METRICS_URL}/update", json=payload, timeout=2)
        except Exception:
            pass


threading.Thread(target=_send_loop,    daemon=True).start()
threading.Thread(target=_metrics_loop, daemon=True).start()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _expand_rate_shorthand(updates: dict, current_tcp_ratio: int) -> dict:
    """If the caller passed `rate` (combined shorthand), expand it into
    `tcp_rate` and `udp_rate` using `tcp_ratio` (from updates or current state).
    Explicit tcp_rate/udp_rate keys in `updates` still take precedence because
    this function runs first and they overwrite the derived values in the
    subsequent state.update() call.

    This makes `{"rate": 20}` work identically to
    `{"tcp_rate": 12, "udp_rate": 8}` at the default tcp_ratio=60.
    """
    if 'rate' not in updates:
        return updates
    updates = dict(updates)          # don't mutate the caller's dict
    combined = updates.pop('rate')   # remove; not a state key
    ratio = updates.get('tcp_ratio', current_tcp_ratio)
    updates.setdefault('tcp_rate', round(combined * ratio / 100, 3))
    updates.setdefault('udp_rate', round(combined * (100 - ratio) / 100, 3))
    return updates


def _expand_packet_size_shorthand(updates: dict) -> dict:
    """If the caller passed the deprecated combined `packet_size`, expand it into
    `tcp_packet_size` and `udp_packet_size` - unless those were also provided
    explicitly, in which case the explicit values win."""
    if 'packet_size' not in updates:
        return updates
    updates = dict(updates)              # don't mutate the caller's dict
    size = updates.pop('packet_size')    # remove; not a state key
    updates.setdefault('tcp_packet_size', size)
    updates.setdefault('udp_packet_size', size)
    return updates


def _expand_burst_rate_shorthand(updates: dict) -> dict:
    """If the caller passed the deprecated combined `burst_rate`, expand it into
    `tcp_burst_rate` and `udp_burst_rate` - unless those were also provided
    explicitly, in which case the explicit values win."""
    if 'burst_rate' not in updates:
        return updates
    updates = dict(updates)                # don't mutate the caller's dict
    rate = updates.pop('burst_rate')       # remove; not a state key
    updates.setdefault('tcp_burst_rate', rate)
    updates.setdefault('udp_burst_rate', rate)
    return updates


# ── REST API ──────────────────────────────────────────────────────────────────

@app.post("/start", response_model=OkResponse, summary="Start generating traffic",
          description="Starts the generator and optionally applies an initial configuration (same fields as PATCH /config).")
async def start(body: GeneratorConfig = Body(default=GeneratorConfig())):
    updates = _expand_rate_shorthand(body.model_dump(exclude_none=True), state["tcp_ratio"])
    updates = _expand_packet_size_shorthand(updates)
    updates = _expand_burst_rate_shorthand(updates)
    with _lock:
        state["running"]      = True
        state["packets_sent"] = 0
        state["bytes_sent"]   = 0
        state["errors"]       = 0
        state.update({k: v for k, v in updates.items() if k in state})
        _history.clear()  # inside lock: _metrics_loop() also holds _lock when appending, so no race
    _stop_requested.clear()   # wake-up flag off → sleeps work normally again
    return {"ok": True}


@app.post("/stop", response_model=OkResponse, summary="Stop generating traffic")
async def stop():
    with _lock:
        state["running"] = False
    _stop_requested.set()   # wake the send loop immediately so it sees running=False
    return {"ok": True}


@app.patch("/config", response_model=OkResponse, summary="Update configuration at runtime",
           description="Updates any subset of: mode ('normal'|'stealth'), pattern "
                        "('constant'|'periodic_burst'|'random'|'ramp'), tcp_rate, udp_rate, "
                        "tcp_packet_size, udp_packet_size (or the deprecated combined "
                        "packet_size), mean_interval, min_size, max_size, tcp_ratio, "
                        "tcp_burst_rate, udp_burst_rate (or the deprecated combined burst_rate), "
                        "burst_duration, burst_interval, ramp_start_rate, "
                        "ramp_end_rate, ramp_duration, fault_rate, extra_latency_ms.")
async def config(body: GeneratorConfig = Body(...)):
    updates = _expand_rate_shorthand(body.model_dump(exclude_none=True), state["tcp_ratio"])
    updates = _expand_packet_size_shorthand(updates)
    updates = _expand_burst_rate_shorthand(updates)
    with _lock:
        state.update({k: v for k, v in updates.items() if k in state})
        mode = state["mode"]
    return {"ok": True, "mode": mode}


@app.get("/status", response_model=StatusResponse, summary="Get live status and statistics")
async def status():
    with _lock:
        result = dict(state)
        result["ramp_progress"] = _ramp_progress(result["pattern"], result["ramp_duration"])
        result["history"] = list(_history)
        return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)