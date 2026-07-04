#!/usr/bin/env bash
# Capture 02 — Temporal Analysis
# --------------------------------
# Assignment requirement:
#   I/O graphs per protocol showing all three temporal patterns:
#     • constant  — flat, steady packet rate (Phase 2: http2_dominant)
#     • burst     — periodic spikes every 30 s (Phase 1: http2_burst, burst_rate 8×)
#     • ramp      — linear rate increase over time (Phase 3: http2_ramp)
#
# Profile:  http2_heavy
#   warmup  15 s  → rates ramp up from 0
#   Phase 1 120 s → http2_burst  (bursts every 30 s, 5 s wide, 50→400 req/s)
#   Phase 2  60 s → http2_dominant (constant 150 req/s)
#   Phase 3  60 s → http2_ramp    (linear 10→200 req/s over 50 s)
#
# Capture: analyzer-http2 (main) + analyzer-quic (side-by-side comparison)
# Duration: 245 s  (all three phases, starting right after warmup)
#
# In Wireshark:
#   Statistics → IO Graphs
#     Add curve: tcp.port==8080   (HTTP/2) — shows burst spikes + ramp slope
#     Add curve: udp.port==4433   (QUIC)   — low constant background
#   Y-Axis: Packets/s,  Interval: 1 s

set -euo pipefail

CONTROLLER="http://localhost:8000"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
MERGECAP="/Applications/Wireshark.app/Contents/MacOS/mergecap"
PROFILE="http2_heavy"
WARMUP=15
CAPTURE_DURATION=245

log() { echo "[$(date +%H:%M:%S)] $*"; }

check_deps() {
    if ! command -v docker &>/dev/null; then echo "ERROR: docker not found." >&2; exit 1; fi
    if [ ! -x "$MERGECAP" ]; then echo "ERROR: mergecap not found at $MERGECAP" >&2; exit 1; fi
}

wait_for_controller() {
    log "Waiting for controller..."
    for i in $(seq 1 20); do
        if curl -sf "$CONTROLLER/status" >/dev/null 2>&1; then log "Controller is up."; return 0; fi
        sleep 2
    done
    echo "ERROR: Controller not reachable. Run: docker-compose up -d" >&2; exit 1
}

check_deps
wait_for_controller

STATUS=$(curl -sf "$CONTROLLER/status" 2>/dev/null || echo "")
if echo "$STATUS" | grep -q '"running": true'; then
    log "System already running — stopping first to load $PROFILE cleanly..."
    curl -sf -X POST "$CONTROLLER/stop" >/dev/null
    sleep 3
fi

log "Starting system with '$PROFILE' profile..."
curl -sf -X POST "$CONTROLLER/start?profile=$PROFILE" >/dev/null
log "System started. Waiting ${WARMUP}s for warmup..."
sleep $((WARMUP + 3))

log "Starting ${CAPTURE_DURATION}s capture on analyzer-http2 and analyzer-quic..."
docker exec analyzer-http2 tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c02_http2.pcapng &
docker exec analyzer-quic  tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c02_quic.pcapng &

log "Capturing... ($CAPTURE_DURATION s — covers burst / constant / ramp phases)"
wait
log "Captures done."

docker cp analyzer-http2:/tmp/c02_http2.pcapng "$OUT_DIR/c02_http2.pcapng"
docker cp analyzer-quic:/tmp/c02_quic.pcapng   "$OUT_DIR/c02_quic.pcapng"

"$MERGECAP" -w "$OUT_DIR/02_temporal_analysis.pcapng" \
    "$OUT_DIR/c02_http2.pcapng" \
    "$OUT_DIR/c02_quic.pcapng"

rm -f "$OUT_DIR"/c02_*.pcapng

log "Done.  Output: $OUT_DIR/02_temporal_analysis.pcapng"
log ""
log "In Wireshark → Statistics → IO Graphs:"
log "  Curve 1: tcp.port==8080  (HTTP/2) — visible: 3 burst spikes, then flat, then rising ramp"
log "  Curve 2: udp.port==4433  (QUIC)   — low constant background"
log "  Y-Axis: Packets/s,  Interval: 1 s"
