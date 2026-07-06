#!/usr/bin/env bash
# Capture 04b — Failure Visibility (lab-sheet T4 procedure)
# -----------------------------------------------------------
# This follows the assignment's T4 test case literally, as opposed to
# capture_04_failure_visibility.sh which stops target-tcpudp/mosquitto.
#
#   curl -X POST "http://localhost:8000/start?profile=balanced"
#   Capture 30 s of normal operation, then, without stopping the capture:
#     docker stop <TARGET_SERVICE>
#   Capture 30 s more.
#
#   Variant A (default): TARGET_SERVICE=target-http2
#     Filter tcp.flags.reset==1.
#     Pass: RST packets from gen-http2 appear within 1 s of the stop
#     command; MQTT and QUIC traffic continues without interruption.
#
#   Variant B: TARGET_SERVICE=mosquitto
#     Filter mqtt.msgtype==14, or watch for repeated TCP SYNs to port 1883.
#
# Usage:
#   cd <project-root>
#   bash captures/capture_04b_failure_visibility.sh                     # variant A (target-http2)
#   TARGET_SERVICE=mosquitto bash captures/capture_04b_failure_visibility.sh   # variant B (mosquitto)
#
# Requirements:
#   - docker-compose stack must already be running (docker-compose up -d)
#   - Wireshark installed at /Applications/Wireshark.app (for mergecap)

set -euo pipefail

CONTROLLER="http://localhost:8000"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
MERGECAP="/Applications/Wireshark.app/Contents/MacOS/mergecap"
PROFILE="balanced"
WARMUP=30

# Which service to stop for this run. Defaults to the assignment's primary
# example (target-http2); set TARGET_SERVICE=mosquitto for the second variant.
TARGET_SERVICE="${TARGET_SERVICE:-target-http2}"

BASELINE_DURATION=30   # seconds captured before the stop
POST_DURATION=30       # seconds captured after the stop
TOTAL_DURATION=$((BASELINE_DURATION + POST_DURATION))

case "$TARGET_SERVICE" in
    target-http2) OUT_NAME="04b_failure_visibility_http2.pcapng" ;;
    mosquitto)    OUT_NAME="04b_failure_visibility_mosquitto.pcapng" ;;
    *)            OUT_NAME="04b_failure_visibility_${TARGET_SERVICE}.pcapng" ;;
esac

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

log "Target service for this run: $TARGET_SERVICE"
if ! docker inspect "$TARGET_SERVICE" >/dev/null 2>&1; then
    echo "ERROR: no container named '$TARGET_SERVICE' found. Check the name (docker ps)." >&2
    exit 1
fi

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

# ── Start all 4 captures in parallel (covers the whole baseline+post window) ──
log "Starting ${TOTAL_DURATION}s capture on all four analyzers..."
docker exec analyzer-http2  tshark -i eth0 -q -f "not port 9090" -a duration:$TOTAL_DURATION -F pcapng -w /tmp/c04b_http2.pcapng  &
docker exec analyzer-quic   tshark -i eth0 -q -f "not port 9090" -a duration:$TOTAL_DURATION -F pcapng -w /tmp/c04b_quic.pcapng   &
docker exec analyzer-mqtt   tshark -i eth0 -q -f "not port 9090" -a duration:$TOTAL_DURATION -F pcapng -w /tmp/c04b_mqtt.pcapng   &
docker exec analyzer-tcpudp tshark -i eth0 -q -f "not port 9090" -a duration:$TOTAL_DURATION -F pcapng -w /tmp/c04b_tcpudp.pcapng &

# ── Baseline window ────────────────────────────────────────────────────────────
log "Capturing ${BASELINE_DURATION}s of normal operation (baseline)..."
sleep "$BASELINE_DURATION"

# ── Inject the failure, without stopping the capture ──────────────────────────
log ">>> t=${BASELINE_DURATION}s  Stopping $TARGET_SERVICE"
docker stop "$TARGET_SERVICE" >/dev/null

# ── Post-failure window ────────────────────────────────────────────────────────
log "Capturing ${POST_DURATION}s more after the stop..."
sleep "$POST_DURATION"

# ── Wait for all tshark processes to finish (should already be done) ──────────
log "Waiting for captures to finish..."
wait
log "Captures done."

# ── Restart the stopped service so the stack is left in a clean state ─────────
log "Restarting $TARGET_SERVICE to leave the system in a clean state..."
docker start "$TARGET_SERVICE" >/dev/null || true

# ── Copy & merge ───────────────────────────────────────────────────────────────
docker cp analyzer-http2:/tmp/c04b_http2.pcapng   "$OUT_DIR/c04b_http2.pcapng"
docker cp analyzer-quic:/tmp/c04b_quic.pcapng     "$OUT_DIR/c04b_quic.pcapng"
docker cp analyzer-mqtt:/tmp/c04b_mqtt.pcapng     "$OUT_DIR/c04b_mqtt.pcapng"
docker cp analyzer-tcpudp:/tmp/c04b_tcpudp.pcapng "$OUT_DIR/c04b_tcpudp.pcapng"

MERGED="$OUT_DIR/$OUT_NAME"
"$MERGECAP" -w "$MERGED" \
    "$OUT_DIR/c04b_http2.pcapng" \
    "$OUT_DIR/c04b_quic.pcapng" \
    "$OUT_DIR/c04b_mqtt.pcapng" \
    "$OUT_DIR/c04b_tcpudp.pcapng"

rm -f "$OUT_DIR"/c04b_*.pcapng

log "Done.  Output: $MERGED"
log ""
if [ "$TARGET_SERVICE" = "target-http2" ]; then
    log "In Wireshark:"
    log "  Filter: tcp.flags.reset==1   → expect RST from gen-http2 within ~1s of t=${BASELINE_DURATION}s"
    log "  Filter: tcp.port==1883 / udp.port==4433 → confirm MQTT/QUIC continue uninterrupted"
elif [ "$TARGET_SERVICE" = "mosquitto" ]; then
    log "In Wireshark:"
    log "  Filter: mqtt.msgtype==14     → expect MQTT DISCONNECT at t=${BASELINE_DURATION}s"
    log "  Filter: tcp.port==1883 && tcp.flags.syn==1 → watch for repeated reconnect SYNs"
    log "  Filter: tcp.port==8080 / udp.port==4433 → confirm HTTP/2 and QUIC continue uninterrupted"
else
    log "Filter tcp.flags.reset==1 / mqtt.msgtype==14 as appropriate for $TARGET_SERVICE."
fi
log ""
log "Run the other variant with:"
log "  TARGET_SERVICE=mosquitto bash $0"
