#!/usr/bin/env bash
# Capture 04c — Failure Visibility: iptables REJECT (live-demo contrast case)
# -----------------------------------------------------------------------------
# Complements capture_04b: that one stops the whole target-http2 CONTAINER
# (network namespace torn down -> 0 RST packets, total silence). This script
# instead leaves target-http2 fully alive and blocks its port from outside
# with an iptables REJECT rule - the container and its network stack stay up,
# so the kernel is still there to answer new SYNs with a genuine TCP RST.
# Together with capture_04b and 05_multisystem's stray-RST finding, this
# gives three distinct, evidenced root causes for "RST or no RST":
#   - docker stop target-http2      -> 0 RST   (namespace gone)
#   - iptables REJECT (this script) -> RST immediately, every time (port
#                                       actively refused, kernel still alive)
#   - multi-machine Wi-Fi link      -> RST occasionally (late/duplicate ACK
#                                       arrives after a connection already
#                                       closed cleanly)
#
# Timeline (t = seconds after capture starts):
#   t=00       capture starts, target-http2 reachable normally (baseline)
#   t=10       iptables REJECT rule inserted -> new SYNs get RST
#   t=40       iptables REJECT rule removed -> traffic recovers
#   t=50       capture ends
#
# Uses the existing analyzer-http2 sidecar (network_mode: "service:target-http2",
# already has tshark + NET_RAW/NET_ADMIN - see docker-compose.yml) to capture,
# and a throwaway nicolaka/netshoot container (--network container:target-http2,
# --cap-add=NET_ADMIN) to apply/remove the iptables rule from the same network
# namespace, exactly like the live-demo command already validated manually.
#
# Usage:
#   cd <project-root>
#   bash captures/capture_04c_failure_visibility_reject.sh
#
# Requirements:
#   - docker-compose stack must already be running (docker-compose up -d)
#   - Wireshark installed at /Applications/Wireshark.app (for the tshark path
#     check only; the actual capture runs inside analyzer-http2)

set -euo pipefail

CONTROLLER="http://localhost:8000"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE="balanced"
WARMUP=30

BASELINE_DURATION=10   # seconds captured before the REJECT rule
REJECT_DURATION=30     # seconds the port stays actively refused
RECOVERY_DURATION=10   # seconds captured after the rule is removed
TOTAL_DURATION=$((BASELINE_DURATION + REJECT_DURATION + RECOVERY_DURATION))

OUT_NAME="04c_failure_visibility_reject.pcapng"

log() { echo "[$(date +%H:%M:%S)] $*"; }

check_deps() {
    if ! command -v docker &>/dev/null; then echo "ERROR: docker not found." >&2; exit 1; fi
}

wait_for_controller() {
    log "Waiting for controller..."
    for i in $(seq 1 20); do
        if curl -sf "$CONTROLLER/status" >/dev/null 2>&1; then log "Controller is up."; return 0; fi
        sleep 2
    done
    echo "ERROR: Controller not reachable. Run: docker-compose up -d" >&2; exit 1
}

reject_rule() {
    local action="$1"  # -A (add) or -D (delete)
    docker run --rm --network container:target-http2 --cap-add=NET_ADMIN nicolaka/netshoot \
        iptables "$action" INPUT -p tcp --dport 8080 -j REJECT --reject-with tcp-reset
}

check_deps
wait_for_controller

if ! docker ps --format '{{.Names}}' | grep -qx analyzer-http2; then
    echo "ERROR: analyzer-http2 container not running. Run: docker-compose up -d" >&2
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

log "Starting ${TOTAL_DURATION}s capture on analyzer-http2..."
docker exec analyzer-http2 tshark -i eth0 -q -f "not port 9090" \
    -a duration:$TOTAL_DURATION -F pcapng -w /tmp/c04c_http2.pcapng &
TSHARK_BG=$!

log "Capturing ${BASELINE_DURATION}s of normal operation (baseline)..."
sleep "$BASELINE_DURATION"

log ">>> t=${BASELINE_DURATION}s  Inserting iptables REJECT on target-http2:8080 (expect RST from here on)"
reject_rule -A

log "Capturing ${REJECT_DURATION}s with the port actively refused..."
sleep "$REJECT_DURATION"

log ">>> t=$((BASELINE_DURATION + REJECT_DURATION))s  Removing iptables REJECT rule (expect recovery)"
reject_rule -D

log "Capturing ${RECOVERY_DURATION}s of recovery..."
sleep "$RECOVERY_DURATION"

log "Waiting for tshark to finish..."
wait "$TSHARK_BG"
log "Capture done."

docker cp analyzer-http2:/tmp/c04c_http2.pcapng "$OUT_DIR/$OUT_NAME"
docker exec analyzer-http2 rm -f /tmp/c04c_http2.pcapng

log "Done. Output: $OUT_DIR/$OUT_NAME"
log ""
log "In Wireshark:"
log "  Filter: tcp.flags.reset==1   -> expect RST packets starting ~t=${BASELINE_DURATION}s, none before"
log "  Statistics -> IO Graph       -> HTTP/2 traffic dips/errors during the REJECT window, recovers after"
