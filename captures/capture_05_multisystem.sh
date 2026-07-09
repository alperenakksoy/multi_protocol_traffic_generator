#!/usr/bin/env bash
# Capture 05 — Multi-System Deployment
# --------------------------------------
# Assignment requirement:
#   Deploy the system across 2 lab machines (generators on one, targets on
#   another). Capture traffic on the INTER-MACHINE link and compare to the
#   single-machine capture (01_protocol_distribution.pcapng).
#
# This script drives the real split deployment (docker-compose.generators.yml
# + docker-compose.targets.yml) end to end. Each machine runs ONE command.
#
# ── On Machine B (targets) — start this FIRST ────────────────────────────
#   git clone <repo> && cd <repo>
#   ./captures/capture_05_multisystem.sh targets
#
#   It builds+starts the target stack, prints Machine B's IP, waits for you
#   to confirm Machine A is running, then captures on the PHYSICAL interface
#   for 70s and saves captures/05_multisystem_<hostname>.pcapng.
#
# ── On Machine A (generators) — start SECOND, using the IP B printed ─────
#   git clone <repo> && cd <repo>
#   ./captures/capture_05_multisystem.sh generators <MACHINE_B_IP>
#
#   It writes .env, builds+starts the generator stack, loads the 'balanced'
#   profile and starts traffic automatically — no manual curl calls needed.
#
# Prerequisites:
#   - Both machines on the same network, reachable from one another
#   - Machine B firewall allows inbound 8080/tcp, 4433/udp, 9999/tcp+udp,
#     1883/tcp, 9090/tcp
#   - tshark/Wireshark installed on Machine B (capture runs on the HOST, on
#     the physical interface — not inside a container)
#
# What to show in the report:
#   • Statistics → Protocol Hierarchy — same distribution as
#     01_protocol_distribution.pcapng, but real host IPs instead of Docker's
#     172.x.x.x range
#   • Conversations → IPv4 — confirms genuine inter-machine traffic
#
# Comparability with 01_protocol_distribution.pcapng:
#   To make this a like-for-like comparison (not just "same profile"), Machine
#   B's capture now matches capture_01's methodology exactly:
#     - same filter: "not port 9090" (excludes generator -> metrics reporting
#       traffic, which only exists on the wire here because the metrics
#       collector sits on Machine B in this deployment)
#     - same timing: capture starts only after WARMUP+5s have elapsed, i.e.
#       pure post-warmup steady-state traffic, not the ramp-up phase

set -euo pipefail

ROLE="${1:-}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_ROOT/captures"
PROFILE="balanced"
WARMUP=30
CAPTURE_DURATION=70
HOSTNAME_LABEL=$(hostname | tr ' ' '-')

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
    cat >&2 <<EOF
Usage:
  Step 1, on Machine B (targets):    $0 targets
  Step 2, on Machine A (generators): $0 generators <MACHINE_B_IP>

Run Step 1 first — it prints Machine B's IP for use in Step 2.
EOF
    exit 1
}

[ -z "$ROLE" ] && usage

detect_iface() {
    if [ -n "${IFACE:-}" ]; then echo "$IFACE"; return; fi
    if command -v ip &>/dev/null; then
        ip -o -4 route show to default | awk '{print $5; exit}'
    else
        route get default 2>/dev/null | awk '/interface:/{print $2}'
    fi
}

own_ip() {
    local iface; iface=$(detect_iface)
    if command -v ip &>/dev/null; then
        ip -o -4 addr show "$iface" | awk '{print $4}' | cut -d/ -f1
    else
        ipconfig getifaddr "$iface" 2>/dev/null || true
    fi
}

find_tshark() {
    if command -v tshark &>/dev/null; then command -v tshark; return; fi
    if [ -x /Applications/Wireshark.app/Contents/MacOS/tshark ]; then
        echo /Applications/Wireshark.app/Contents/MacOS/tshark; return
    fi
    die "tshark not found. Install Wireshark (which includes tshark) on this machine."
}

check_deps() {
    command -v docker &>/dev/null || die "docker not found."
    command -v docker-compose &>/dev/null || die "docker-compose not found."
    command -v curl &>/dev/null || die "curl not found."
}

# ── Machine B: targets ──────────────────────────────────────────────────────
run_targets() {
    check_deps
    cd "$REPO_ROOT"

    log "Building and starting the target stack (docker-compose.targets.yml)..."
    docker-compose -f docker-compose.targets.yml up -d --build

    log "Waiting for services to become reachable..."
    for i in $(seq 1 30); do
        curl -sf http://localhost:9090/metrics >/dev/null 2>&1 && break
        sleep 2
    done
    curl -sf http://localhost:9090/metrics >/dev/null 2>&1 \
        || die "metrics never came up. Check: docker-compose -f docker-compose.targets.yml logs"

    local ip; ip=$(own_ip)
    local iface; iface=$(detect_iface)
    [ -z "$ip" ] && die "Could not auto-detect this machine's IP. Set IFACE=<name> and rerun, e.g.: IFACE=en0 $0 targets"

    echo
    log "Machine B is ready."
    log "  IP address:        $ip"
    log "  Capture interface: $iface"
    echo
    log ">>> Now on Machine A, run:"
    log ">>>     ./captures/capture_05_multisystem.sh generators $ip"
    echo
    read -r -p "Press ENTER as soon as Machine A has started generating traffic (right after its 'Loading profile...' log line)... " _

    # Match capture_01's methodology exactly: only start capturing once the
    # profile's warmup ramp has fully completed, so this window is pure
    # steady-state traffic, not a mix of ramp-up + steady-state.
    log "Waiting $((WARMUP + 5))s for Machine A's warmup to finish before capturing (matches 01_protocol_distribution.pcapng's post-warmup window)..."
    sleep $((WARMUP + 5))

    local tshark_bin; tshark_bin=$(find_tshark)
    local outfile="$OUT_DIR/05_multisystem_${HOSTNAME_LABEL}.pcapng"

    log "Capturing on $iface for ${CAPTURE_DURATION}s (filtered: not port 9090, same as capture 01)..."
    if ! "$tshark_bin" -i "$iface" -q -f "not port 9090" -a duration:$CAPTURE_DURATION -F pcapng -w "$outfile"; then
        die "tshark failed — it usually needs elevated privileges. Try: sudo $0 targets (or fix capture permissions for your Wireshark install)."
    fi

    log "Done. Output: $outfile"
    log ""
    log "Compare with $OUT_DIR/01_protocol_distribution.pcapng in Wireshark:"
    log "  Statistics -> Protocol Hierarchy   (same distribution, same filter, both post-warmup)"
    log "  Statistics -> Conversations -> IPv4 (real host IPs, not Docker's 172.x.x.x)"
}

# ── Machine A: generators ───────────────────────────────────────────────────
run_generators() {
    local b_ip="${2:-}"
    [ -z "$b_ip" ] && die "Missing Machine B IP. Usage: $0 generators <MACHINE_B_IP>"

    check_deps
    cd "$REPO_ROOT"

    log "Checking that Machine B ($b_ip) is reachable..."
    curl -sf --max-time 5 "http://$b_ip:9090/metrics" >/dev/null 2>&1 \
        || die "Cannot reach http://$b_ip:9090/metrics — check the IP, the firewall on Machine B (ports 8080,4433/udp,9999,1883,9090), and that 'targets' was started there first."

    echo "TARGET_B_IP=$b_ip" > .env
    log "Wrote .env (TARGET_B_IP=$b_ip)"

    log "Building and starting the generator stack (docker-compose.generators.yml)..."
    docker-compose -f docker-compose.generators.yml up -d --build

    log "Waiting for controller..."
    for i in $(seq 1 30); do
        curl -sf http://localhost:8000/status >/dev/null 2>&1 && break
        sleep 2
    done
    curl -sf http://localhost:8000/status >/dev/null 2>&1 \
        || die "Controller never came up. Check: docker-compose -f docker-compose.generators.yml logs controller"

    log "Loading profile '$PROFILE' and starting traffic..."
    curl -sf -X POST "http://localhost:8000/start?profile=$PROFILE" >/dev/null

    local total=$((WARMUP + CAPTURE_DURATION + 15))
    log "Traffic is running for ${total}s (covers Machine B's ${CAPTURE_DURATION}s capture window + warmup + buffer)."
    log "Switch to Machine B now and press ENTER there right away — it will auto-wait $((WARMUP + 5))s"
    log "for the warmup ramp to finish before it actually starts capturing, so no manual timing needed."
    sleep "$total"

    log "Stopping traffic..."
    curl -sf -X POST "http://localhost:8000/stop" >/dev/null || true

    log "Done. Collect the .pcapng file from Machine B (captures/05_multisystem_*.pcapng)."
}

case "$ROLE" in
    targets)    run_targets ;;
    generators) run_generators "$@" ;;
    *) usage ;;
esac
