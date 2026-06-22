# System Architecture

**MIC Final Project: Multi-Protocol Traffic Generation and Analysis**

---

## Overview

The system is built around the **Producer-Consumer pattern**: generators produce traffic, target services receive it, the metrics collector aggregates the numbers, and the controller coordinates everything through a REST API. The dashboard only reads from and writes to the controller; it never accesses the generators directly.

### Design decision: why no event bus?

During the design phase, the question arose whether the controller should distribute commands through a message bus (such as MQTT itself) or directly over HTTP. We use **direct HTTP calls** because:
- They are easier to debug (logs are immediately readable)
- They involve fewer moving parts (no additional broker needed for control)
- REST is intuitive to explain during the demo

---

## Network design

All containers run in the same Docker network `mic-net` (bridge). This means:
- Containers address each other **by name**: `gen-http2` can call `http://target-http2:8080`
- No port mapping is needed for internal communication
- Only the ports that need to be exposed to the outside (host) are mapped in `docker-compose.yml`

```
Host machine (your laptop / lab PC)
    │
    │   :3000 (Dashboard)
    │   :8000 (Controller API)
    │   :9090 (Metrics)
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  Docker Network: mic-net                            │
│                                                     │
│  controller:8000   ←──►  gen-http2                  │
│  dashboard:3000    ←──►  gen-quic                   │
│  metrics:9090      ←──►  gen-mqtt    ←──►  mosquitto:1883  │
│  target-http2:8080 ←──►  gen-tcpudp                 │
│  target-quic:4433                                   │
└─────────────────────────────────────────────────────┘
```

---

## Container descriptions

### 1. Traffic Controller (`controller/`)

**Technology**: Python 3.11 + FastAPI + Uvicorn

**Role**: The brain of the system. It reads the YAML configuration and translates it into concrete HTTP commands for the generators. It logs every configuration change with a timestamp.

**Why FastAPI?** We need an async REST API; FastAPI is the standard choice for this in Python and natively supports HTTP/2 through Uvicorn.

**API endpoints:**
```
POST /start?profile=<name>        -> Load a YAML profile, run warmup -> phases -> cooldown
POST /stop                        -> Stop the phase runner, Adaptive Control, all generators
POST /config/load                 -> Set the active profile (applies phase 1 if running)
PATCH /generator/{name}            -> Forward arbitrary overrides to one generator's /config
POST /generator/{name}/start       -> Start a single generator only
POST /generator/{name}/stop        -> Stop a single generator only
GET  /status                       -> Full status: running/phase/ramp, all generators, metrics, log
GET  /profiles                     -> List available YAML profiles in config/
GET  /log                          -> Full configuration / adaptive-control log
GET  /adaptive/status              -> Current Adaptive Control state + last decision per generator
POST /adaptive/toggle?enabled=bool -> Manually enable/disable Adaptive Control
GET  /health                       -> Liveness check
```
Full schemas for every field: `docs/api/swagger.html` (combined Swagger UI for all 5 services).

**Configuration log example:**
```
[2026-06-05 14:32:11] LOAD_CONFIG profile=mqtt_heavy
[2026-06-05 14:32:15] START all_generators
[2026-06-05 14:33:01] PATCH gen-http2 rate=200
[2026-06-05 14:35:00] PHASE_CHANGE http2_dominant -> balanced
```

---

### 2. HTTP/2 Traffic Generator (`generators/http2/`)

**Technology**: Python + `httpx` (HTTP/2 capable)

**Configurable parameters** (field names exactly as accepted by `PATCH /config` / the YAML `protocols.http2` block):
```yaml
http2:
  rate: 100                  # Requests per second (across all concurrent streams)
  payload_size: 4096         # Bytes per POST body
  method_get_pct: 70         # 70% GET, 30% POST
  concurrent_streams: 5      # Multiplexed streams fired per cycle
  get_paths: ["/", "/api/items"]    # one chosen at random per GET (default: ["/"])
  post_paths: ["/data"]             # one chosen at random per POST (default: ["/data"])
  pattern: constant          # constant | periodic_burst | random
  burst_rate: 400            # req/s during a burst window (pattern=periodic_burst)
  burst_duration: 5          # seconds per burst window
  burst_interval: 30         # seconds between burst windows
  fault_rate: 0.0            # 0-1: fraction of requests simulated as failures
  extra_latency_ms: 0        # artificial delay before every request
```
The target (`http://target-http2:8080`) is configured once via the `TARGET_URL` environment variable in `docker-compose.yml`, not per-phase in the YAML.

**Important for Wireshark**: HTTP/2 runs over TCP. In Wireshark, you will see TCP connections on port 8080, and the Follow TCP Stream view reveals the HTTP/2 frames. With multiplexing, one TCP connection carries multiple parallel streams; this is the main difference from HTTP/1.1.

---

### 3. QUIC/HTTP/3 Traffic Generator (`generators/quic/`)

**Technology**: Python + `aioquic`

**Configurable parameters:**
```yaml
quic:
  rate: 50               # Connection cycles per second
  payload_size: 1024     # Bytes per HTTP/3 POST, on every stream
  stream_count: 3        # Parallel multiplexed streams per cycle
  use_0rtt: false        # Cache + offer the TLS session ticket for 0-RTT resumption
  pattern: constant      # constant | random
  fault_rate: 0.0
  extra_latency_ms: 0
```
`zero_rtt_used` (read-only, in `/status`) reports whether 0-RTT resumption actually succeeded on the current connection — distinct from `use_0rtt`, which only expresses the *intent*. Target host/port (`target-quic:4433`) are set once via `TARGET_HOST`/`TARGET_PORT` environment variables, not per-phase YAML fields. The generator keeps a single QUIC connection open across many requests (instead of reconnecting per request) so the TLS handshake cost doesn't dominate the measured latency.

**Why QUIC is interesting for Wireshark**: QUIC runs over **UDP**, not TCP. This is unusual for application-level traffic. In Wireshark, you will see UDP packets on port 4433. QUIC frames are encrypted (TLS 1.3), so the payload is not readable, but connection IDs, packet sizes, and timings are visible.

**Implementation note**: QUIC is the technically most demanding protocol. If time becomes tight, implement it last. A working system with 4 protocols and excellent analysis is better than an incomplete system with 5.

---

### 4. MQTT Traffic Generator (`generators/mqtt/`)

**Technology**: Python + `paho-mqtt`

**Configurable parameters:**
```yaml
mqtt:
  rate: 30               # Messages per second
  payload_size: 256      # Bytes
  topic_count: 5         # How many topics to rotate through (1-20) for publish + subscribe
  qos: 1                 # Fixed QoS, used unless qos_distribution is set
  qos_distribution:      # Optional: weighted random QoS per publish (overrides `qos`)
    0: 50                # 50% QoS 0 (fire and forget)
    1: 30                # 30% QoS 1 (at least once)
    2: 20                # 20% QoS 2 (exactly once)
  pattern: constant      # constant | random
  fault_rate: 0.0
  extra_latency_ms: 0
```
The broker (`mosquitto:1883`) is configured once via `BROKER_HOST`/`BROKER_PORT` environment variables. Topics are **not** named individually in the YAML — `topic_count` selects how many of the built-in topics (`sensors/temperature`, `sensors/humidity`, `sensors/pressure`, `actuators/control`, `status/heartbeat`, then generic `load/topic-N`) are used. **Known limitation**: some YAML profiles also list an explicit `topics:` array for documentation/readability; this field is accepted (`extra="allow"` on the Pydantic model) but currently has **no effect** on the generator — only `topic_count` does. If you want named topics to matter, this would need to be wired into `generators/mqtt/generator.py`'s `_topic_list()`.

This generator also **subscribes** to its own topic set, so the broker's fan-out (PUBLISH → broker → subscriber) is visible in captures too, not just the publish-side traffic.

**Important for Wireshark**: MQTT runs over TCP port 1883. In Wireshark, the filter `mqtt` shows all MQTT packets. With QoS 2, you will see a 4-way handshake (PUBLISH -> PUBREC -> PUBREL -> PUBCOMP), which produces more packets than QoS 0 with the same payload. This explains why a higher QoS increases the packet rate even at a lower message rate.

---

### 5. TCP/UDP Raw Traffic Generator (`generators/tcpudp/`)

**Technology**: Python, plain `socket` module (TCP: one new connection per packet via `connect()`/`sendall()`/`close()`; UDP: connectionless `sendto()`). *Note: the original design considered Scapy for raw packet crafting, but the implementation uses standard sockets — simpler, and the per-packet TCP connect/close cycle already produces the SYN/SYN-ACK/FIN sequence needed for the Wireshark analysis.*

This is the most important generator for the **Behavioral Fingerprinting analysis**. It has two **independent** configuration axes: `mode` (packet size + base interval) and `pattern` (overall sending cadence — see below).

**Normal Mode** (recognizable fingerprint) — fixed size, fixed interval derived from `tcp_rate`/`udp_rate`:
```python
def _normal_params():
    size = state["packet_size"]              # FIXED, e.g. 512B
    interval = 1.0 / rate                    # REGULAR, e.g. every 100ms at rate=10
    return size, interval, proto
```

**Stealth Mode** (no recognizable fingerprint) — random size, Poisson-distributed interval:
```python
def _stealth_params():
    size = random.randint(min_size, max_size)        # RANDOM, e.g. 64-1400B
    interval = np.random.exponential(mean_interval)  # POISSON-DISTRIBUTED
    return size, interval, proto
```

Independently of `mode`, the `pattern` field shapes the overall sending cadence on top of that base interval: `constant` (use the interval as-is), `periodic_burst` (send `burst_size` packets back-to-back, then idle `burst_interval` seconds), or `random` (an *additional* Poisson gap on top of the mode's own interval).

Configuration:
```yaml
tcpudp:
  mode: normal           # normal | stealth
  pattern: constant      # constant | periodic_burst | random
  tcp_rate: 20            # TCP packets/sec (normal mode)
  udp_rate: 10            # UDP packets/sec (normal mode)
  packet_size: 512        # Bytes (normal mode, fixed)
  mean_interval: 0.100    # Poisson mean, seconds (stealth mode)
  min_size: 64             # Bytes (stealth mode)
  max_size: 1400           # Bytes (stealth mode)
  tcp_ratio: 60            # 60% TCP, 40% UDP
  burst_size: 10           # packets per burst (pattern=periodic_burst)
  burst_interval: 1.0      # seconds idle between bursts (pattern=periodic_burst)
  fault_rate: 0.0
  extra_latency_ms: 0
```

Detailed explanation of stealth mode: see [`STEALTH_MODE.md`](STEALTH_MODE.md).

---

### 6. Target Services

**HTTP/2 Server** (`targets/http2_server/`):
- Hypercorn (ASGI server with HTTP/2 support) + FastAPI
- Responds to GET with JSON, to POST with an echo
- Logs every request for metrics

**QUIC Server** (`targets/quic_server/`):
- aioquic-based server
- Port 4433/UDP
- TLS certificate (self-signed, generated inside the container)

**MQTT Broker** (Mosquitto, official Docker image):
- Standard Eclipse Mosquitto
- Configured with `config/mosquitto.conf`
- No authentication (for lab purposes)

---

### 7. Metrics Collector (`metrics/`)

**Technology**: Python + Flask

Every generator sends its status to the collector every 5 seconds:
```json
{
  "generator": "gen-http2",
  "timestamp": "2026-06-05T14:33:45Z",
  "packets_sent": 15420,
  "bytes_transferred": 63078400,
  "errors": 3,
  "active_connections": 5,
  "current_rate": 98.7
}
```

The collector aggregates this and exposes it at `/metrics`:
```json
{
  "total_packets": 89234,
  "total_bytes": 412847102,
  "by_protocol": {
    "http2": {"packets": 31200, "errors": 3},
    "quic":  {"packets": 18900, "errors": 0},
    "mqtt":  {"packets": 24100, "errors": 1},
    "tcp":   {"packets": 9800,  "errors": 0},
    "udp":   {"packets": 5234,  "errors": 2}
  },
  "uptime_seconds": 847
}
```

---

## Autonomous features (beyond the baseline requirements)

### Warmup / Cooldown / Ramping

The `global.warmup`/`global.cooldown` YAML fields are implemented in the controller's `_ramp_rates()` function: rate fields are linearly stepped from 0 up to the configured target (warmup, at profile start) or from the target down to 0 (cooldown, at profile end), in up to 20 steps via periodic `PATCH /config` calls. This is the *same* mechanism that implements the "ramping (linear increase over time)" sending pattern required by the assignment — the gradual change is directly visible as a rising/falling slope in a Wireshark I/O graph. The dashboard shows live ramp progress (`ramp_status.progress`, 0.0–1.0) while either is active.

### Random (Poisson) pattern on all 4 protocols

All 4 generators (not just TCP/UDP) support `pattern: random`, implemented as `random.expovariate(1/interval)` (or `np.random.exponential(interval)` for TCP/UDP) instead of a fixed `sleep(interval)` — exponentially-distributed inter-arrival times with the same mean rate as `constant`, i.e. a Poisson process. `config/balanced.yaml`'s `balanced_with_stealth` phase demonstrates this on all protocols simultaneously, alongside TCP/UDP stealth mode.

### Fault Injection

Every generator accepts `fault_rate` (0–1, fraction of sends simulated as failures *without* actually sending) and `extra_latency_ms` (artificial delay before every send). Controllable per-protocol from the dashboard's "Fault Injection" panel — used to demonstrate Analysis Task 4 (Failure Visibility) and to drive Adaptive Control reactions live.

### Adaptive Control

An autonomous closed-loop controller (`_adaptive_loop()` in `controller/main.py`): every `check_interval` seconds, it computes each generator's error rate (and latency, where reported) since the last check, and multiplicatively scales that generator's rate up (`scale_up_factor`) or down (`scale_down_factor`) against configurable thresholds, bounded by `min_multiplier`/`max_multiplier`. Enabled either via a phase's `adaptive_control` block in the YAML (e.g. `balanced.yaml`'s `balanced_adaptive` phase) or manually via `POST /adaptive/toggle`. This goes beyond the assignment's baseline requirements but directly demonstrates "resilience patterns" from the course objectives.

---

## Data flow

```
                    ┌──────────┐
Browser ──────────► │Dashboard │
                    └────┬─────┘
                         │ REST (HTTP)
                    ┌────▼─────┐
                    │Controller│ ◄── YAML Config
                    └────┬─────┘
                         │ HTTP commands to generators
         ┌───────────────┼────────────────────┐
         ▼               ▼                    ▼
    ┌─────────┐     ┌─────────┐          ┌─────────┐
    │gen-http2│     │gen-quic │   ...    │gen-tcpudp│
    └────┬────┘     └────┬────┘          └────┬────┘
         │               │                    │
         │ HTTP/2        │ QUIC/UDP           │ TCP/UDP
         ▼               ▼                    ▼
    ┌─────────┐     ┌─────────┐          [no target,
    │target-  │     │target-  │           raw packets]
    │http2    │     │quic     │
    └─────────┘     └─────────┘
         │               │
         └───────┬────────┘
                 │ Stats
                 ▼
           ┌──────────┐
           │ metrics  │ ◄── all generators send stats
           └──────────┘
                 ▲
    Dashboard ───┘ (GET /metrics every 2 sec)
```

---

## Multi-Machine Deployment

For Analysis Task 5, the system is distributed across 2 lab machines:

**Machine A (generators)**:
```yaml
# docker-compose.generators.yml
services:
  controller: ...
  gen-http2: ...
  gen-quic: ...
  gen-mqtt: ...
  gen-tcpudp: ...
```

**Machine B (targets)**:
```yaml
# docker-compose.targets.yml
services:
  target-http2: ...
  target-quic: ...
  mosquitto: ...
  metrics: ...
```

The generators on machine A point to the IP address of machine B via the `TARGET_B_IP` environment variable (set in a `.env` file next to `docker-compose.generators.yml`, copy from `.env.example`). Wireshark runs on the **physical** network interface of either machine (not `docker0`/`br-*`), so the traffic is genuinely inter-machine rather than looped back over the Docker bridge. Machine B's firewall must allow inbound connections on 8080/tcp, 4433/udp, 9999/tcp+udp, 1883/tcp, 9090/tcp.

---

## Implementation order (recommended)

```
Week 1: Foundation
  ├── docker-compose.yml skeleton
  ├── Mosquitto + gen-mqtt -> working?
  └── HTTP/2 server + gen-http2

Week 2: Core
  ├── Controller + YAML config
  ├── Metrics collector
  ├── TCP/UDP generator (normal mode)
  └── QUIC (last, most difficult part)

Week 3: Polish
  ├── Stealth mode in the TCP/UDP generator
  ├── Dashboard
  ├── Adaptive phase logic in the controller
  └── Wireshark captures for all 5 analyses
```

---

*Detailed step-by-step instructions for each container are in the respective subfolder README.*
