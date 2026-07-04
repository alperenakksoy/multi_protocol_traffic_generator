#!/usr/bin/env bash
# Capture 03 — Behavioral Fingerprinting
# ----------------------------------------
# Assignment requirement:
#   Packet size distributions, inter-arrival times, and connection patterns
#   per protocol. Document which protocols are distinguishable (and which overlap).
#
# Profile:  tcpudp_heavy
#   warmup   10 s
#   Phase 1  60 s → tcp_dominant  (TCP 80 pkt/s, 1400 B — SYN/ACK overhead visible)
#   Phase 2  60 s → udp_dominant  (UDP 150 pkt/s, 1400 B — no handshake overhead)
#   Phase 3  60 s → tcp_burst     (TCP burst every 15 s → spike in Packet Lengths)
#   Phase 4  60 s → mixed_transport (TCP + UDP + MQTT 20 pkt/s — all three in one)
#
# Capture: analyzer-tcpudp (TCP vs UDP contrast, main fingerprinting signal)
#          analyzer-mqtt   (tiny 64 B payloads — stark contrast to 1400 B TCP/UDP)
# Duration: 250 s  (all four phases after warmup)
#
# Key fingerprinting observations for the report:
#   • TCP:  bimodal packet sizes (data=1400 B + ACK=~60 B), regular inter-arrivals
#   • UDP:  unimodal packet sizes (only data=1400 B, no ACK), lower inter-arrival variance
#   • MQTT: tiny packets (64-256 B), QoS 1/2 creates pairs/quads visible in size histogram
#   • HTTP/2: large payloads (2048+ B), bursty even at "constant" rate (stream multiplexing)
#
# In Wireshark:
#   Statistics → Packet Lengths    (shows size distribution per protocol after filtering)
#   Statistics → IO Graphs         (inter-arrival pattern: constant vs random vs burst)
#   Filter tcp.port==9999           then udp.port==9999  then tcp.port==1883

set -euo pipefail

CONTROLLER="http://localhost:8000"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
MERGECAP="/Applications/Wireshark.app/Contents/MacOS/mergecap"
PROFILE="tcpudp_heavy"
WARMUP=10
CAPTURE_DURATION=250

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
log "System started. Waiting ${WARMUP}s for warmup..."
sleep $((WARMUP + 3))

log "Starting ${CAPTURE_DURATION}s capture on analyzer-tcpudp and analyzer-mqtt..."
docker exec analyzer-tcpudp tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c03_tcpudp.pcapng &
docker exec analyzer-mqtt   tshark -i eth0 -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w /tmp/c03_mqtt.pcapng &

log "Capturing... ($CAPTURE_DURATION s — tcp_dominant → udp_dominant → tcp_burst → mixed)"
wait
log "Captures done."

docker cp analyzer-tcpudp:/tmp/c03_tcpudp.pcapng "$OUT_DIR/c03_tcpudp.pcapng"
docker cp analyzer-mqtt:/tmp/c03_mqtt.pcapng     "$OUT_DIR/c03_mqtt.pcapng"

"$MERGECAP" -w "$OUT_DIR/03_behavioral_fingerprinting.pcapng" \
    "$OUT_DIR/c03_tcpudp.pcapng" \
    "$OUT_DIR/c03_mqtt.pcapng"

rm -f "$OUT_DIR"/c03_*.pcapng

log "Done.  Output: $OUT_DIR/03_behavioral_fingerprinting.pcapng"
log ""
log "In Wireshark:"
log "  Filter: tcp.port==9999   → Statistics → Packet Lengths  (TCP: bimodal 60B + 1400B)"
log "  Filter: udp.port==9999   → Statistics → Packet Lengths  (UDP: unimodal 1400B only)"
log "  Filter: tcp.port==1883   ��� Statistics → Packet Lengths  (MQTT: tiny 64-256 B)"
log "  IO Graphs all three overlaid → different spike/gap patterns per protocol"
