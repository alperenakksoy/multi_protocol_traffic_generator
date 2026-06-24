"""
QUIC / HTTP/3 Traffic Generator
---------------------------------
Sends HTTP/3 POST requests with a configurable payload over QUIC using
aioquic. Uses a self-signed certificate from the QUIC target server
(no verification).

Request rate, payload size (bytes sent as the HTTP/3 request body), the
number of concurrently multiplexed streams per cycle, and 0-RTT session
resumption are all configurable at runtime.

The overall sending cadence is controlled by `pattern`: 'constant' (fixed
1/rate interval between cycles) or 'random' (exponentially-distributed/
Poisson gaps between cycles, with the same mean rate as 'constant') - useful
for the Temporal Analysis task.

Exposes a small REST API so the Traffic Controller can start/stop/reconfigure
this generator at runtime and read its live statistics.
"""

import os, sys, time, asyncio, threading, ssl, random
from typing import Optional

import requests as req_sync
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

app = FastAPI(
    title="MIC QUIC/HTTP3 Generator",
    description="Generates configurable HTTP/3 requests over QUIC against the QUIC target server.",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TARGET_HOST = os.getenv("TARGET_HOST", "target-quic")
TARGET_PORT = int(os.getenv("TARGET_PORT", "4433"))
METRICS_URL = os.getenv("METRICS_URL", "http://metrics:9090")
PORT        = int(os.getenv("PORT", "7002"))

state = {
    "running":      False,
    "rate":         20,
    "payload_size": 512,
    "stream_count": 1,
    "use_0rtt":     False,
    "pattern":      "constant",  # "constant" | "random"
    "packets_sent": 0,
    "bytes_sent":   0,
    "errors":       0,
    "rate_bps":     0,
    "latency_ms":   0,
    "fault_rate":         0.0,   # 0.0-1.0: probability that a request is simulated as a failure (no real send)
    "extra_latency_ms":   0,     # artificial extra delay injected before each request
    "zero_rtt_used":      False, # read-only: whether the current connection actually resumed via 0-RTT
}

_lock = threading.Lock()
_bytes_window: list[tuple[float, int]] = []
_latency_window: list[tuple[float, float]] = []

# Ring buffer of recent metric snapshots (one per _metrics() tick, ~5s apart),
# capped to the last 5 minutes. Exposed via /status so the dashboard can draw
# live rate/latency/error sparklines without polling a separate endpoint.
_HISTORY_MAXLEN = 60
_history: list[dict] = []

# Cached TLS session ticket from the most recent connection. When use_0rtt is
# enabled, this is handed to aioquic on the next (re)connect so the handshake
# can be resumed via 0-RTT instead of a full 1-RTT handshake. Only accessed
# from the single background asyncio thread, so no lock is needed.
_session_ticket = None


def _on_session_ticket(ticket):
    """Called by aioquic when the server issues a NewSessionTicket. Cached so
    a future connection can attempt 0-RTT resumption with it."""
    global _session_ticket
    _session_ticket = ticket


# ── Silence a known aioquic/H3 cosmetic issue ───────────────────────────────
# When an HTTP/3 connection closes, aioquic's asyncio adapter may still hold
# StreamWriter objects for *peer-initiated unidirectional* streams (e.g. the
# server's QPACK encoder/decoder streams). Their __del__ tries to send a FIN
# on that stream, which aioquic correctly rejects with a ValueError. This is
# harmless (the connection is already closing) but spams stderr on every
# request. Filter out just this known, benign message.
_default_unraisablehook = sys.unraisablehook

def _quiet_aioquic_unraisablehook(unraisable):
    msg = str(unraisable.exc_value) if unraisable.exc_value else ""
    if "peer-initiated unidirectional stream" in msg:
        return
    _default_unraisablehook(unraisable)

sys.unraisablehook = _quiet_aioquic_unraisablehook


# ── Models (Swagger) ────────────────────────────────────────────────────────

class GeneratorConfig(BaseModel):
    """Configurable parameters. All fields optional; only provided keys are updated."""
    model_config = ConfigDict(extra="allow", json_schema_extra={
        "example": {"rate": 50, "payload_size": 1024, "stream_count": 3, "use_0rtt": False,
                     "fault_rate": 0.0, "extra_latency_ms": 0}
    })
    rate: Optional[float] = Field(
        None, ge=0,
        description="Target send rate in connection cycles per second; each cycle sends one HTTP/3 "
                     "POST request on every concurrent stream."
    )
    payload_size: Optional[int] = Field(
        None, ge=0,
        description="Size (bytes) of the random payload sent as the body of each HTTP/3 POST "
                     "request, on every stream."
    )
    stream_count: Optional[int] = Field(
        None, ge=1, le=20,
        description="Number of HTTP/3 requests fired concurrently each cycle, all multiplexed as "
                     "independent streams over the single shared QUIC connection to the target."
    )
    use_0rtt: Optional[bool] = Field(
        None,
        description="If true, cache the TLS session ticket from the server and attempt 0-RTT "
                     "session resumption on the next (re)connect, skipping a full 1-RTT handshake. "
                     "Whether resumption actually happened is reported as zero_rtt_used in /status."
    )
    pattern: Optional[str] = Field(
        None,
        description="Overall sending cadence: 'constant' (fixed 1/rate interval between cycles) "
                     "or 'random' (exponentially-distributed/Poisson gaps between cycles, same "
                     "mean rate as 'constant')."
    )
    fault_rate: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Fraction of requests (0-1) deliberately treated as failures, without sending, "
                     "for resilience and failure-injection demos."
    )
    extra_latency_ms: Optional[float] = Field(
        None, ge=0,
        description="Artificial extra delay (ms) injected before every request, simulating "
                     "network congestion or an overloaded target."
    )


class StatusResponse(BaseModel):
    running: bool
    rate: float
    payload_size: int
    stream_count: int
    use_0rtt: bool
    pattern: str
    zero_rtt_used: bool
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


# Fields reported in /status but not settable via /start or PATCH /config,
# even though they live in the same `state` dict.
_READONLY_FIELDS = {"zero_rtt_used"}


# ── QUIC send using aioquic ─────────────────────────────────────────────────
#
# Design note on connection reuse:
# A QUIC connection starts with a TLS 1.3 handshake, which costs at least one
# extra round trip before any HTTP/3 request can be sent. If a new connection
# were opened for every single request (as a naive implementation might do),
# that handshake cost would dominate the measured latency and make QUIC look
# far slower than the other protocols, which all keep a connection or socket
# open across requests (HTTP/2 via a persistent httpx client, MQTT via a
# persistent broker connection, TCP/UDP via a persistent socket).
#
# To keep the comparison fair, this generator opens one QUIC connection and
# reuses it for many requests, only reconnecting if the connection breaks or
# the generator is stopped and restarted. This matches how a real HTTP/3
# client behaves.

async def _send_on_connection(conn, h3):
    """Send one cycle of HTTP/3 POST requests on an already-open QUIC connection."""
    with _lock:
        fault_rate    = state["fault_rate"]
        extra_latency = state["extra_latency_ms"]
        streams       = max(1, state["stream_count"])
        payload_size  = max(0, state["payload_size"])

    t0 = time.perf_counter()

    if extra_latency > 0:
        await asyncio.sleep(extra_latency / 1000)

    if fault_rate > 0 and random.random() < fault_rate:
        # Injected fault: simulate a failed request without sending it.
        elapsed_ms = (time.perf_counter() - t0) * 1000
        with _lock:
            state["errors"] += 1
        _latency_window.append((time.time(), elapsed_ms))
        return

    # Same random payload reused across all streams in this cycle (avoids
    # repeated os.urandom() calls at high stream counts); each stream is an
    # independent multiplexed HTTP/3 POST carrying `payload_size` bytes.
    payload = os.urandom(payload_size)
    for _ in range(streams):
        stream_id = conn._quic.get_next_available_stream_id()
        h3.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"POST"),
                (b":path",   b"/data"),
                (b":scheme", b"https"),
                (b":authority", TARGET_HOST.encode()),
                (b"content-length", str(payload_size).encode()),
            ],
        )
        h3.send_data(stream_id=stream_id, data=payload, end_stream=True)
    conn.transmit()

    # Short wait for the response frames. The connection is already
    # established, so this covers one round trip, not a fresh handshake.
    await asyncio.sleep(0.005)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    with _lock:
        state["packets_sent"] += streams
        state["bytes_sent"]   += payload_size * streams
    _bytes_window.append((time.time(), payload_size * streams))
    _latency_window.append((time.time(), elapsed_ms))


async def _run_connection():
    """Open one QUIC connection and keep sending requests on it until the
    generator is stopped or the connection breaks (then the caller retries)."""
    from aioquic.asyncio.client import connect
    from aioquic.h3.connection import H3_ALPN, H3Connection
    from aioquic.quic.configuration import QuicConfiguration

    with _lock:
        use_0rtt = state["use_0rtt"]
        state["zero_rtt_used"] = False  # reset; set True below only on a confirmed resumption

    config = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN)
    config.verify_mode = ssl.CERT_NONE  # self-signed cert on target

    # If 0-RTT is enabled and we have a session ticket from a previous
    # connection, offer it so aioquic can attempt 0-RTT resumption: the
    # client can send early (0-RTT) data using keys derived from the cached
    # ticket, before the 1-RTT handshake with the server completes.
    cached_ticket = _session_ticket if use_0rtt else None
    if cached_ticket is not None:
        config.session_ticket = cached_ticket

    async with connect(
        TARGET_HOST, TARGET_PORT, configuration=config,
        session_ticket_handler=_on_session_ticket,
    ) as conn:
        # Whether this connection actually resumed the cached session (i.e.
        # 0-RTT/PSK resumption succeeded) vs. falling back to a full handshake.
        zero_rtt_used = bool(cached_ticket is not None and conn._quic.tls.session_resumed)
        with _lock:
            state["zero_rtt_used"] = zero_rtt_used

        h3 = H3Connection(conn._quic, enable_webtransport=False)

        while True:
            with _lock:
                running = state["running"]
                rate    = state["rate"]
                pattern = state["pattern"]

            if not running:
                return  # exits the "async with" and closes the connection cleanly

            if rate <= 0:
                await asyncio.sleep(0.1)
                continue

            await _send_on_connection(conn, h3)

            interval = 1.0 / rate
            if pattern == "random":
                # Exponential inter-arrival time => Poisson process, same mean rate.
                await asyncio.sleep(random.expovariate(1.0 / interval))
            else:
                await asyncio.sleep(interval)


async def _generate():
    while True:
        with _lock:
            running = state["running"]

        if not running:
            await asyncio.sleep(0.1)
            continue

        try:
            await _run_connection()
        except Exception:
            # Connection could not be established or broke mid-stream.
            with _lock:
                state["errors"] += 1
            await asyncio.sleep(1.0)  # back off before reconnecting


async def _metrics():
    while True:
        await asyncio.sleep(5)
        now    = time.time()
        recent = [b for ts, b in _bytes_window if now - ts <= 10]
        _bytes_window[:] = [(ts, b) for ts, b in _bytes_window if now - ts <= 10]
        rate_bps = sum(recent) / 10 if recent else 0

        recent_lat = [l for ts, l in _latency_window if now - ts <= 10]
        _latency_window[:] = [(ts, l) for ts, l in _latency_window if now - ts <= 10]
        avg_latency = sum(recent_lat) / len(recent_lat) if recent_lat else 0

        with _lock:
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
                "generator":    "gen-quic",
                "running":      state["running"],
                "packets_sent": state["packets_sent"],
                "bytes_sent":   state["bytes_sent"],
                "errors":       state["errors"],
                "rate_bps":     state["rate_bps"],
                "rate":         state["rate"],
                "latency_ms":   state["latency_ms"],
            }

        try:
            req_sync.post(f"{METRICS_URL}/update", json=payload, timeout=2)
        except Exception:
            pass


def _run_background():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(_generate())
    loop.create_task(_metrics())
    loop.run_forever()


threading.Thread(target=_run_background, daemon=True).start()


# ── REST API ──────────────────────────────────────────────────────────────────

@app.post("/start", response_model=OkResponse, summary="Start generating traffic",
          description="Starts the generator and optionally applies an initial configuration (same fields as PATCH /config).")
async def start(body: GeneratorConfig = Body(default=GeneratorConfig())):
    with _lock:
        state["running"] = True
        state.update({k: v for k, v in body.model_dump(exclude_none=True).items()
                       if k in state and k not in _READONLY_FIELDS})
    return {"ok": True}


@app.post("/stop", response_model=OkResponse, summary="Stop generating traffic")
async def stop():
    with _lock:
        state["running"] = False
    return {"ok": True}


@app.patch("/config", response_model=OkResponse, summary="Update configuration at runtime",
           description="Updates any subset of: rate, payload_size, stream_count, use_0rtt, "
                        "pattern ('constant'|'random'), fault_rate, extra_latency_ms. Note: "
                        "use_0rtt only affects the next (re)connect; zero_rtt_used in /status "
                        "reports whether 0-RTT resumption actually occurred on the current connection.")
async def config(body: GeneratorConfig = Body(...)):
    with _lock:
        state.update({k: v for k, v in body.model_dump(exclude_none=True).items()
                       if k in state and k not in _READONLY_FIELDS})
    return {"ok": True}


@app.get("/status", response_model=StatusResponse, summary="Get live status and statistics")
async def status():
    with _lock:
        result = dict(state)
        result["history"] = list(_history)
        return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
