#!/usr/bin/env bash
# Capture 04 — Failure Visibility
# ---------------------------------
# Assignment requirement:
#   Deliberately stop a generator/target while capturing. Document the
#   protocol-specific failure signals and how fast failure becomes visible:
#     • TCP RST      — visible the instant target-tcpudp is stopped
#     • MQTT DISCONNECT (msgtype=14) — visible when mosquitto is stopped
#     • QUIC silence — no explicit close frame visible (encrypted); failure
#                      signal is the absence of UDP packets on port 4433
#
# Timeline (t = seconds after capture starts):
#   t=00  capture starts, all 4 protocols running normally (baseline)
#   t=30  target-tcpudp stopped  → TCP RST packets appear immediately
#   t=48  target-tcpudp restarted → TCP reconnect visible (SYN/SYN-ACK/ACK)
#   t=65  mosquitto stopped       → MQTT DISCONNECT + silence on port 1883
#   t=88  mosquitto restarted     → MQTT CONNECT/CONNACK visible
#   t=120 capture ends
#
# Capture: all 4 analyzers simultaneously, merged into one file
#
# In Wireshark:
#   Filter: tcp.flags.reset==1          → TCP RST at t≈30 s
#   Filter: mqtt.msgtype==14            → MQTT DISCONNECT at t≈65 s
#   Filter: udp.port==4433              → QUIC silence gap at t≈65 s
#   Statistics → IO Graphs (all protocols) → visible silence gaps

set -euo pipefail

CONTROLLER="http://localhost:8000"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
MERGECAP="/Applications/Wireshark.app/Contents/MacOS/mergecap"
PROFILE="balanced"
WARMUP=30
CAPTURE_DURATION=125

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
    log "Stopping current system to load $PROFILE cleanly..."
    curl -sf -X POST "$CONTROLLER/stop" >/dev/null
    sleep 3
fi

log "Starting system with '$PROFILE' profile..."
curl -sf -X POST "$CONTROLLER/start?profile=$PROFILE" >/dev/null
log "System started. Waiting ${WARMUP}s for warmup + stable baseline..."
sleep $((WARMUP + 5))

# ── Start all 4 captures in parallel ───���─────────────────────────────────────
log "Starting ${CAPTURE_DURATION}s capture on all four analyzers..."
docker exec analyzer-http2  tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c04_http2.pcapng  &
docker exec analyzer-quic   tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c04_quic.pcapng   &
docker exec analyzer-mqtt   tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c04_mqtt.pcapng   &
docker exec analyzer-tcpudp tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c04_tcpudp.pcapng &

# ── Inject failures at precise timestamps within the capture window ───────────

# t=30 s: stop TCP/UDP target → TCP RST
sleep 30
log ">>> t=30s  Stopping target-tcpudp  (expect TCP RST in capture)"
docker stop target-tcpudp >/dev/null

# t=48 s: restart TCP/UDP target → TCP reconnect
sleep 18
log ">>> t=48s  Restarting target-tcpudp  (expect TCP SYN in capture)"
docker start target-tcpudp >/dev/null

# t=65 s: stop mosquitto → MQTT DISCONNECT + QUIC-style silence
sleep 17
log ">>> t=65s  Stopping mosquitto  (expect MQTT DISCONNECT + silence on port 1883)"
docker stop mosquitto >/dev/null

# t=88 s: restart mosquitto
sleep 23
log ">>> t=88s  Restarting mosquitto  (expect MQTT CONNECT/CONNACK in capture)"
docker start mosquitto >/dev/null

# ── Wait for all tshark processes to finish ���──────────────────────────────────
log "Waiting for captures to complete..."
wait
log "Captures done."

# ── Copy & merge ───────────────────────────────────────���──────────────────────
docker cp analyzer-http2:/tmp/c04_http2.pcapng   "$OUT_DIR/c04_http2.pcapng"
docker cp analyzer-quic:/tmp/c04_quic.pcapng     "$OUT_DIR/c04_quic.pcapng"
docker cp analyzer-mqtt:/tmp/c04_mqtt.pcapng     "$OUT_DIR/c04_mqtt.pcapng"
docker cp analyzer-tcpudp:/tmp/c04_tcpudp.pcapng "$OUT_DIR/c04_tcpudp.pcapng"

"$MERGECAP" -w "$OUT_DIR/04_failure_visibility.pcapng" \
    "$OUT_DIR/c04_http2.pcapng" \
    "$OUT_DIR/c04_quic.pcapng" \
    "$OUT_DIR/c04_mqtt.pcapng" \
    "$OUT_DIR/c04_tcpudp.pcapng"

rm -f "$OUT_DIR"/c04_*.pcapng

log "Done.  Output: $OUT_DIR/04_failure_visibility.pcapng"
log ""
log "In Wireshark — apply these filters one by one and screenshot each:"
log "  tcp.flags.reset==1       → TCP RST packet(s) at ~t=30 s"
log "  mqtt.msgtype==14         → MQTT DISCONNECT packet at ~t=65 s"
log "  udp.port==4433           → QUIC silence gap starting at ~t=65 s"
log "  (no filter, IO Graph)    → all 4 protocols, silence gaps clearly visible"
