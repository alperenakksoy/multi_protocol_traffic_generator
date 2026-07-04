#!/usr/bin/env bash
# Capture 05 — Multi-System Deployment
# --------------------------------------
# Assignment requirement:
#   Run the system on 2+ lab machines. Capture inter-machine traffic and
#   compare to the single-machine capture (01_protocol_distribution.pcapng).
#   Show: same protocol distribution, different IP addresses per machine.
#
# This script runs identically on BOTH machines.
# Run it on Machine A and Machine B at roughly the same time.
# Each machine saves its own capture; label them A and B in the report.
#
# Output file: 05_multisystem_<HOSTNAME>.pcapng
#   e.g.  05_multisystem_lab-pc-12.pcapng   (Machine A)
#         05_multisystem_lab-pc-17.pcapng   (Machine B)
#
# Prerequisites on each machine:
#   1. git clone <repo>  (or copy the project folder)
#   2. docker-compose up -d
#   3. Run this script
#
# What to show in the report:
#   • Open both pcapng files in Wireshark side-by-side
#   • Statistics → Protocol Hierarchy on both — same distribution, different IPs
#   • Conversations tab — IP addresses confirm these are separate machines
#   • This proves the system deploys independently on any Docker-capable host

set -euo pipefail

CONTROLLER="http://localhost:8000"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
MERGECAP="/Applications/Wireshark.app/Contents/MacOS/mergecap"
PROFILE="balanced"
WARMUP=30
CAPTURE_DURATION=70
HOSTNAME_LABEL=$(hostname | tr ' ' '-')

log() { echo "[$(date +%H:%M:%S)] $*"; }

check_deps() {
    if ! command -v docker &>/dev/null; then echo "ERROR: docker not found." >&2; exit 1; fi
    # mergecap optional on non-Mac machines — skip merge if not present
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
    log "System already running — using current state."
else
    log "Starting system with '$PROFILE' profile..."
    curl -sf -X POST "$CONTROLLER/start?profile=$PROFILE" >/dev/null
    log "System started. Waiting ${WARMUP}s for warmup..."
    sleep $((WARMUP + 3))
fi

log "Starting ${CAPTURE_DURATION}s capture on all four analyzers (machine: $HOSTNAME_LABEL)..."
docker exec analyzer-http2  tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c05_http2.pcapng  &
docker exec analyzer-quic   tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c05_quic.pcapng   &
docker exec analyzer-mqtt   tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c05_mqtt.pcapng   &
docker exec analyzer-tcpudp tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c05_tcpudp.pcapng &

log "Capturing... ($CAPTURE_DURATION s)"
wait
log "Captures done."

docker cp analyzer-http2:/tmp/c05_http2.pcapng   "$OUT_DIR/c05_http2.pcapng"
docker cp analyzer-quic:/tmp/c05_quic.pcapng     "$OUT_DIR/c05_quic.pcapng"
docker cp analyzer-mqtt:/tmp/c05_mqtt.pcapng     "$OUT_DIR/c05_mqtt.pcapng"
docker cp analyzer-tcpudp:/tmp/c05_tcpudp.pcapng "$OUT_DIR/c05_tcpudp.pcapng"

OUTFILE="$OUT_DIR/05_multisystem_${HOSTNAME_LABEL}.pcapng"

if [ -x "$MERGECAP" ]; then
    "$MERGECAP" -w "$OUTFILE" \
        "$OUT_DIR/c05_http2.pcapng" \
        "$OUT_DIR/c05_quic.pcapng" \
        "$OUT_DIR/c05_mqtt.pcapng" \
        "$OUT_DIR/c05_tcpudp.pcapng"
    rm -f "$OUT_DIR"/c05_*.pcapng
    log "Done.  Output: $OUTFILE"
else
    # On Linux machines without Wireshark, keep the separate files
    for f in "$OUT_DIR"/c05_*.pcapng; do
        mv "$f" "${f/c05_/05_multisystem_${HOSTNAME_LABEL}_}"
    done
    log "Done.  Output files: $OUT_DIR/05_multisystem_${HOSTNAME_LABEL}_*.pcapng"
    log "  (Copy these to your Mac and merge with mergecap if needed)"
fi

log ""
log "Copy this file to your Mac and open alongside 01_protocol_distribution.pcapng."
log "In Wireshark → Statistics → Conversations → IPv4"
log "  The IP addresses confirm this is a separate machine — same protocol distribution."
