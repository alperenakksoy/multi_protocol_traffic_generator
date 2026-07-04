#!/usr/bin/env bash
# Capture 01 — Protocol Distribution
# ------------------------------------
# Starts the system with the 'balanced' profile, waits for warmup,
# captures 70 s from all four analyzer containers in parallel,
# copies the results to captures/, and merges them into one pcapng.
#
# Usage:
#   cd <project-root>
#   bash captures/capture_01_protocol_distribution.sh
#
# Requirements:
#   - docker-compose stack must already be running (docker-compose up -d)
#   - Wireshark installed at /Applications/Wireshark.app (for mergecap)

set -euo pipefail

CONTROLLER="http://localhost:8000"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
MERGECAP="/Applications/Wireshark.app/Contents/MacOS/mergecap"

# ── Helpers ───────────────────────────────────────────────────────────────────

log() { echo "[$(date +%H:%M:%S)] $*"; }

check_deps() {
    if ! command -v docker &>/dev/null; then
        echo "ERROR: docker not found." >&2; exit 1
    fi
    if [ ! -x "$MERGECAP" ]; then
        echo "ERROR: mergecap not found at $MERGECAP" >&2
        echo "       Install Wireshark from https://www.wireshark.org/" >&2
        exit 1
    fi
}

wait_for_controller() {
    log "Waiting for controller to be reachable..."
    for i in $(seq 1 20); do
        if curl -sf "$CONTROLLER/status" >/dev/null 2>&1; then
            log "Controller is up."
            return 0
        fi
        sleep 2
    done
    echo "ERROR: Controller not reachable at $CONTROLLER after 40 s." >&2
    echo "       Make sure docker-compose is running: docker-compose up -d" >&2
    exit 1
}

# ── Main ──────────────────────────────────────────────────────────────────────

check_deps
wait_for_controller

# Start system with balanced profile (idempotent if already running)
STATUS=$(curl -sf "$CONTROLLER/status" 2>/dev/null || echo "")
if echo "$STATUS" | grep -q '"running": true'; then
    log "System already running — using current state."
else
    log "Starting system with 'balanced' profile..."
    curl -sf -X POST "$CONTROLLER/start?profile=balanced" >/dev/null
    log "System started."
fi

# Wait for warmup (balanced has 30 s warmup) + a few seconds buffer
log "Waiting 35 s for warmup to complete before capturing..."
sleep 35

# ── Parallel captures ─────────────────────────────────────────────────────────

log "Starting 70 s capture on all four analyzers..."
docker exec analyzer-http2  tshark -i eth0 -q -f "not port 9090" -a duration:70 -F pcapng -w /tmp/c01_http2.pcapng  &
docker exec analyzer-quic   tshark -i eth0 -q -f "not port 9090" -a duration:70 -F pcapng -w /tmp/c01_quic.pcapng   &
docker exec analyzer-mqtt   tshark -i eth0 -q -f "not port 9090" -a duration:70 -F pcapng -w /tmp/c01_mqtt.pcapng   &
docker exec analyzer-tcpudp tshark -i eth0 -q -f "not port 9090" -a duration:70 -F pcapng -w /tmp/c01_tcpudp.pcapng &

log "Capturing... (70 s)"
wait
log "Captures done."

# ── Copy to host ──────────────────────────────────────────────────────────────

log "Copying pcapng files to $OUT_DIR/ ..."
docker cp analyzer-http2:/tmp/c01_http2.pcapng   "$OUT_DIR/c01_http2.pcapng"
docker cp analyzer-quic:/tmp/c01_quic.pcapng     "$OUT_DIR/c01_quic.pcapng"
docker cp analyzer-mqtt:/tmp/c01_mqtt.pcapng     "$OUT_DIR/c01_mqtt.pcapng"
docker cp analyzer-tcpudp:/tmp/c01_tcpudp.pcapng "$OUT_DIR/c01_tcpudp.pcapng"

# ── Merge ─────────────────────────────────────────────────────────────────────

MERGED="$OUT_DIR/01_protocol_distribution.pcapng"
log "Merging into $MERGED ..."
"$MERGECAP" -w "$MERGED" \
    "$OUT_DIR/c01_http2.pcapng" \
    "$OUT_DIR/c01_quic.pcapng" \
    "$OUT_DIR/c01_mqtt.pcapng" \
    "$OUT_DIR/c01_tcpudp.pcapng"

# Clean up individual files (keep only the merged result)
rm -f "$OUT_DIR"/c01_*.pcapng

log "Done.  Output: $MERGED"
log ""
log "Open in Wireshark, then:"
log "  Statistics → Protocol Hierarchy   (export as PNG for the report)"
log "  Statistics → IO Graphs            (packets/s per protocol)"
