# MIC Final Project: Multi-Protocol Traffic Generation and Analysis

**Hochschule Rhein-Waal · Mobile & Internet Computing · SS2026**

---

## Progress

- [x] System architecture & Docker Compose setup
- [x] Protocol generators
- [x] Traffic controller
- [x] Configurable traffic patterns (3+ YAML profiles, ramp/burst/random)
- [x] Dashboard for live control and monitoring
- [x] Report (Related Work)
- [ ] Multi-Machine-Test / (Lab)
- [ ] Wireshark captures
- [ ] System Evaluation
- [ ] Live demo rehearsal

---

## What this project does

This system simulates a realistic enterprise network by generating concurrent traffic across five protocols (HTTP/2, QUIC/HTTP/3, MQTT, TCP, and UDP), all from orchestrated Docker containers. A central controller reads a YAML configuration file and steers all generators in real time. A web dashboard lets you watch and control everything live.

The system also includes a **Stealth Mode** for the TCP/UDP generator: instead of fixed packet sizes and intervals, it uses randomized sizes and Poisson-distributed timing so the traffic is statistically indistinguishable from real user activity, directly demonstrating **Behavioral Fingerprinting** (Analysis Task 3).

Beyond the baseline requirements, the controller also implements: global **warmup/cooldown ramps** (rates linearly ramp from/to 0, visible as slopes in Wireshark I/O graphs), a **Random/Poisson sending pattern** on all 4 generators (not just TCP/UDP) for Temporal Analysis, **Fault Injection** (`fault_rate`, `extra_latency_ms` per protocol) for resilience demos, and an autonomous **Adaptive Control** loop that scales each generator's rate up or down based on its observed error rate and latency, with no manual intervention. All of this is controllable live from the dashboard.

---

## Quick Start

```bash
git clone <repo>
cd mic-final-project

# Build and start everything
docker-compose build
docker-compose up

# Dashboard: http://localhost:3000
# Controller API: http://localhost:8000
# Metrics: http://localhost:9090/metrics
```

> **Needs**: Docker Desktop, `docker-compose` v2+. No other local dependencies.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         docker-compose.yml                          │
│                          Network: mic-net                           │
└─────────────────────────────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
┌───────────────┐       ┌───────────────┐       ┌───────────────┐
│   CONTROLLER  │       │   DASHBOARD   │       │   MOSQUITTO   │
│   :8000 REST  │       │   :3000 Web   │       │   :1883 MQTT  │
│               │◄──────│               │       │               │
│ Reads YAML    │       │ Minimalist UI │       │ MQTT Broker   │
│ Coordinates   │       │ for the demo  │       │               │
└───────┬───────┘       └───────────────┘       └───────┬───────┘
        │                                               │
        │  starts / stops / configures                  │
        │                                               │
┌───────┴──────────────────────────────────────┐        │
│                   Generators                 │        │
│                                              │        │
│  ┌──────────────┐   ┌──────────────┐         │        │
│  │  HTTP/2 Gen  │   │  QUIC Gen    │         │        │
│  │  (httpx)     │   │  (aioquic)   │         │        │
│  └──────┬───────┘   └──────┬───────┘         │        │
│         │                 │                  │        │
│  ┌──────┴───────┐   ┌──────┴───────┐         │        │
│  │  MQTT Gen    │   │  TCP/UDP Gen │         │        │
│  │  (paho-mqtt) │   │  Normal Mode │◄──────  │        │
│  └──────┬───────┘   │  Stealth Mode│ YAML    │        │
│         │           └──────────────┘         │        │
└─────────┼────────────────────────────────────┘        │
          └─────────────────────────────────────────────┘
                                │
       ┌────────────────────────┼────────────────────────┐
       │                        │                        │
       ▼                        ▼                        ▼
┌──────────────┐        ┌──────────────┐        ┌──────────────┐
│ HTTP/2 Server│        │ QUIC Server  │        │   METRICS    │
│ Hypercorn +  │        │ (aioquic)    │        │  COLLECTOR   │
│ FastAPI :8080│        │ :4433 UDP    │        │  :9090/metrics│
└──────────────┘        └──────────────┘        └──────────────┘
```

---

## Container Overview

| Container | Image | Port | Role |
|---|---|---|---|
| `controller` | Python + FastAPI | 8000 | Reads YAML, controls generators, REST API |
| `dashboard` | Static HTML/JS served via Python `http.server` | 3000 | Live dashboard for the demo |
| `gen-http2` | Python + httpx | - | HTTP/2 GET/POST to `target-http2` |
| `gen-quic` | Python + aioquic | - | QUIC/HTTP/3 requests to `target-quic` |
| `gen-mqtt` | Python + paho-mqtt | - | MQTT publish/subscribe via Mosquitto |
| `gen-tcpudp` | Python (raw `socket`s, TCP + UDP) | - | Raw TCP/UDP, normal + stealth mode, constant/periodic-burst/random patterns |
| `target-http2` | Hypercorn + FastAPI | 8080 | HTTP/2 server |
| `target-quic` | aioquic | 4433 | QUIC server |
| `mosquitto` | Eclipse Mosquitto | 1883 | MQTT broker |
| `metrics` | Python + Flask | 9090 | Aggregates stats from all generators + analyzers |
| `analyzer-http2`, `analyzer-quic`, `analyzer-mqtt`, `analyzer-tcpudp` | Python + `tshark` | - | Live packet-capture sidecars (one per target's network namespace); real protocol distribution + I/O graph, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#8-network-analyzer-analyzer) |

---

## Directory Structure

```
mic-final-project/
├── README.md                        ← you are here
├── docker-compose.yml               ← starts everything (single machine)
├── docker-compose.generators.yml    ← Machine A: controller + dashboard + generators (multi-machine)
├── docker-compose.targets.yml       ← Machine B: targets + broker + metrics (multi-machine)
├── .env.example                     ← copy to .env on Machine A, set TARGET_B_IP
│
├── config/
│   ├── http2_heavy.yaml             ← Profile 1: HTTP/2 dominant
│   ├── mqtt_heavy.yaml              ← Profile 2: MQTT dominant
│   ├── balanced.yaml                ← Profile 3: balanced
│   └── mosquitto.conf               ← MQTT broker configuration
│
├── controller/
│   ├── Dockerfile
│   ├── main.py                      ← FastAPI REST API
│   └── requirements.txt
│
├── generators/
│   ├── http2/
│   │   ├── Dockerfile
│   │   └── generator.py
│   ├── quic/
│   │   ├── Dockerfile
│   │   └── generator.py
│   ├── mqtt/
│   │   ├── Dockerfile
│   │   └── generator.py
│   └── tcpudp/
│       ├── Dockerfile
│       └── generator.py             ← Normal + Stealth Mode
│
├── targets/
│   ├── http2_server/
│   │   ├── Dockerfile
│   │   └── server.py
│   └── quic_server/
│       ├── Dockerfile
│       └── server.py
│
├── metrics/
│   ├── Dockerfile
│   └── collector.py
│
├── dashboard/
│   ├── Dockerfile                   ← serves index.html via `python -m http.server`
│   └── index.html                   ← single-file dashboard (HTML + CSS + vanilla JS)
│
├── docs/
│   ├── ARCHITECTURE.md              ← Detailed architecture explanation
│   ├── WIRESHARK_GUIDE.md           ← Step-by-step capture guide
│   ├── STEALTH_MODE.md              ← Traffic obfuscation, the key feature
│   ├── DEMO_SCRIPT.md               ← 15-20 min demo script
│   └── api/
│       ├── swagger.html             ← combined Swagger UI for all 5 services
│       └── openapi_*.json           ← generated OpenAPI specs (regenerate after API changes)
│
└── captures/                        ← NOT YET CREATED — see "Wireshark Captures" below
    ├── 01_protocol_distribution.pcapng
    ├── 02_temporal_analysis.pcapng
    ├── 03_behavioral_fingerprinting.pcapng
    ├── 04_failure_visibility.pcapng
    └── 05_multi_machine.pcapng
```

---

## Configuration Profiles

The system reads YAML configurations from `config/`. At least 3 profiles are provided:

```bash
# Switch profile while the system is running:
curl -X POST http://localhost:8000/config/load \
  -H "Content-Type: application/json" \
  -d '{"profile": "mqtt_heavy"}'
```

See [`config/`](config/) for all profiles and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full configuration schema.

---

## Controller REST API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/start?profile=<name>` | Loads a YAML profile and starts the full phase/warmup/cooldown run |
| `POST` | `/stop` | Stops the phase runner, Adaptive Control, and all generators |
| `POST` | `/config/load` | Sets the active profile (applies phase 1 immediately if running) |
| `PATCH` | `/generator/{name}` | Forwards arbitrary key/value overrides to one generator (e.g. `{"rate": 80}`) |
| `POST` | `/generator/{name}/start` | Starts a single generator without affecting the others |
| `POST` | `/generator/{name}/stop` | Stops a single generator without affecting the others |
| `GET` | `/status` | Full system status: running/phase/ramp state, all generators' live status, aggregated metrics, last 50 log entries |
| `GET` | `/profiles` | Lists available YAML profiles in `config/` |
| `GET` | `/log` | Full timestamped configuration/adaptive-control log |
| `GET` | `/adaptive/status` | Current Adaptive Control state and last decision per generator |
| `POST` | `/adaptive/toggle?enabled=<bool>` | Manually enables/disables Adaptive Control, independent of the active profile |
| `GET` | `/health` | Liveness check |

Full interactive documentation (all 5 services, request/response schemas): open `docs/api/swagger.html` in a browser, or run any service and visit its own `/docs` (FastAPI's built-in Swagger UI).

Each generator additionally exposes its own `POST /start`, `POST /stop`, `PATCH /config`, `GET /status` (the controller's `/generator/*` endpoints are thin proxies to these).

---

## Multi-Machine Deployment (Analysis Task 5)

Two extra Compose files split the system across 2 lab machines. `captures/capture_05_multisystem.sh` drives the whole thing — one command per machine:

```bash
# Step 1, on Machine B (targets) — FIRST:
./captures/capture_05_multisystem.sh targets

# Step 2, on Machine A (generators), using the IP Machine B printed:
./captures/capture_05_multisystem.sh generators <MACHINE_B_IP>
```

It builds/starts the right Compose file on each side, wires up `TARGET_B_IP` automatically, starts traffic on Machine A, and captures 70s on Machine B's **physical** interface (not `docker0`/`br-*`) to see genuine inter-machine traffic — saved as `captures/05_multisystem_<hostname>.pcapng`. See [`docs/WIRESHARK_GUIDE.md`](docs/WIRESHARK_GUIDE.md) (Capture 5) for the full walkthrough.

---

## The Stealth Mode Feature

The TCP/UDP generator has two modes:

**Normal Mode**: a fixed fingerprint, immediately recognizable in Wireshark:
- Packet size: always 512 bytes
- Interval: always 100ms

**Stealth Mode**: no recognizable pattern:
- Packet size: random, 64-1400 bytes
- Interval: Poisson-distributed (mean = 100ms)

The result is directly visible in Wireshark; see [`docs/STEALTH_MODE.md`](docs/STEALTH_MODE.md).

---

## Wireshark Captures

Five captures need to be created. The exact step-by-step instructions for each capture are in [`docs/WIRESHARK_GUIDE.md`](docs/WIRESHARK_GUIDE.md).

| # | Task | Duration | What to show |
|---|---|---|---|
| 1 | Protocol Distribution | 60 sec | Protocol Hierarchy screenshot |
| 2 | Temporal Analysis | 120 sec | I/O graph with burst phases |
| 3 | Behavioral Fingerprinting | 60 sec | Normal vs. stealth packet sizes |
| 4 | Failure Visibility | 30 sec | TCP RST / MQTT disconnect |
| 5 | Multi-Machine | 60 sec | Inter-machine link traffic |

---

## Grading Map

| Criterion | Weight | How it is fulfilled |
|---|---|---|
| System completeness | 25% | All 15 containers run with `docker-compose up` |
| Configuration flexibility | 15% | 3 YAML profiles plus runtime changes via API |
| Traffic analysis depth | 25% | 5 Wireshark captures plus stealth mode analysis |
| Multi-system deployment | 10% | 2 lab machines, capture on the link |
| Report quality | 15% | IEEE format, all sections, AI documentation |
| Live demo | 10% | Dashboard plus config switch plus failure demo |

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): why each container is built the way it is
- [`docs/WIRESHARK_GUIDE.md`](docs/WIRESHARK_GUIDE.md): exact capture instructions for all 5 analyses
- [`docs/STEALTH_MODE.md`](docs/STEALTH_MODE.md): traffic obfuscation: theory, implementation, results
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md): the 15-20 minute demo script

---

*MIC Final Project · SS2026 · Hochschule Rhein-Waal*
