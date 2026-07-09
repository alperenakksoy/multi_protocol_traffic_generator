#!/usr/bin/env python3
"""
Failure Timeline: Protocol Isolation During docker stop/start (Capture 04)
----------------------------------------------------------------------------
Recreates Wireshark's "Statistics -> I/O Graph" for 04_failure_visibility.pcapng
as an annotated matplotlib figure: one packets/sec line per protocol (HTTP/2,
QUIC, MQTT, Raw TCP/UDP combined), with vertical markers at the four scripted
events. The story: three lines keep flowing undisturbed, exactly one line
drops to zero at each stop and recovers at each restart - direct visual
evidence of protocol isolation.

Also computes "time to silence" per stopped service: the gap between the
docker stop command and the last packet observed on that service's port,
plus a sanity check that no tcp.flags.reset==1 / mqtt.msgtype==14 (DISCONNECT)
packets appear anywhere in the capture - confirming the report's "silence,
not RST" finding numerically, not just by inspection.

Event timeline (from capture_04_failure_visibility.sh):
    t=30s  docker stop  target-tcpudp   (expect: raw TCP/UDP line -> 0)
    t=48s  docker start target-tcpudp   (expect: raw TCP/UDP line recovers)
    t=65s  docker stop  mosquitto       (expect: MQTT line -> 0)
    t=88s  docker start mosquitto       (expect: MQTT line recovers)

Usage:
    python3 captures/analysis/04_failure/failure_timeline.py

Requires: tshark (Wireshark), matplotlib, numpy.
Reads:    captures/04_failure_visibility.pcapng
Writes:   captures/analysis/04_failure/failure_timeline.png
          captures/analysis/04_failure/failure_timeline_summary.txt
"""

import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
PCAP = REPO_ROOT / "captures" / "04_failure_visibility.pcapng"

BIN_SECONDS = 1.0

SERIES = [
    ("HTTP/2", "tcp.port==8080", "#C44E52"),
    ("QUIC", "udp.port==4433", "#4C72B0"),
    ("MQTT", "tcp.port==1883", "#55A868"),
    ("Raw TCP/UDP", "(tcp.port==9999 or udp.port==9999)", "#8172B2"),
]

EVENTS = [
    (30, "stop target-tcpudp", "Raw TCP/UDP"),
    (48, "start target-tcpudp", "Raw TCP/UDP"),
    (65, "stop mosquitto", "MQTT"),
    (88, "start mosquitto", "MQTT"),
]


def find_tshark() -> str:
    found = shutil.which("tshark")
    if found:
        return found
    mac_path = Path("/Applications/Wireshark.app/Contents/MacOS/tshark")
    if mac_path.is_file():
        return str(mac_path)
    sys.exit("ERROR: tshark not found. Install Wireshark or add tshark to PATH.")


def extract_timestamps(tshark: str, pcap: Path, display_filter: str) -> np.ndarray:
    if not pcap.is_file():
        sys.exit(f"ERROR: capture file not found: {pcap}")
    cmd = [
        tshark, "-r", str(pcap),
        "-Y", display_filter,
        "-T", "fields", "-e", "frame.time_relative",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return np.array([float(line) for line in result.stdout.splitlines() if line.strip()])


def count_matches(tshark: str, pcap: Path, display_filter: str) -> int:
    if not pcap.is_file():
        sys.exit(f"ERROR: capture file not found: {pcap}")
    cmd = [tshark, "-r", str(pcap), "-Y", display_filter]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return len([line for line in result.stdout.splitlines() if line.strip()])


def bin_per_second(timestamps: np.ndarray, duration: float) -> tuple[np.ndarray, np.ndarray]:
    n_bins = int(np.ceil(duration / BIN_SECONDS))
    edges = np.arange(0, n_bins + 1) * BIN_SECONDS
    counts, _ = np.histogram(timestamps, bins=edges)
    centers = edges[:-1] + BIN_SECONDS / 2
    return centers, counts


def last_packet_before_gap(timestamps: np.ndarray, stop_t: float, search_window: float = 10.0) -> float | None:
    """Last packet timestamp in (stop_t, stop_t+search_window] - i.e. how long
    traffic straggled on after the stop command before going fully silent."""
    window = timestamps[(timestamps > stop_t) & (timestamps <= stop_t + search_window)]
    return float(window.max()) if window.size else None


def main() -> None:
    tshark = find_tshark()
    print(f"Using tshark: {tshark}")

    all_ts = {}
    max_time = 0.0
    for label, display_filter, _ in SERIES:
        print(f"Extracting {label} ({display_filter}) ...")
        ts = extract_timestamps(tshark, PCAP, display_filter)
        all_ts[label] = ts
        if ts.size:
            max_time = max(max_time, ts.max())
    duration = float(np.ceil(max_time))

    binned = {label: bin_per_second(all_ts[label], duration) for label, _, _ in SERIES}

    # ── Time-to-silence per stop event ──────────────────────────────────────
    summary_lines = [f"Capture duration: {duration:.1f}s", ""]
    for stop_t, description, affected in [(30, "stop target-tcpudp", "Raw TCP/UDP"),
                                           (65, "stop mosquitto", "MQTT")]:
        last_t = last_packet_before_gap(all_ts[affected], stop_t)
        if last_t is None:
            summary_lines.append(f"t={stop_t}s {description}: silence is immediate (no packets in the 10s after stop)")
        else:
            gap = last_t - stop_t
            summary_lines.append(f"t={stop_t}s {description}: last {affected} packet at t={last_t:.2f}s (+{gap:.2f}s after stop command)")

    # ── Sanity check: RST / DISCONNECT counts (expect 0, per report finding) ─
    rst_count = count_matches(tshark, PCAP, "tcp.flags.reset==1")
    disconnect_count = count_matches(tshark, PCAP, "mqtt.msgtype==14")
    summary_lines += [
        "",
        f"tcp.flags.reset==1 packets in whole capture: {rst_count}",
        f"mqtt.msgtype==14 (DISCONNECT) packets in whole capture: {disconnect_count}",
        "(both expected 0 - docker stop tears down the network namespace before",
        " any RST/DISCONNECT can be sent; see report Section VIII-A)",
    ]

    summary_text = "\n".join(summary_lines)
    print("\n" + summary_text)
    (SCRIPT_DIR / "failure_timeline_summary.txt").write_text(summary_text + "\n")

    # ── Plot ─────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(11, 5))

    for label, _, color in SERIES:
        centers, counts = binned[label]
        ax.plot(centers, counts, label=label, color=color, linewidth=1.2)

    ymax = max(counts.max() for _, counts in binned.values())
    for t, description, _ in EVENTS:
        ax.axvline(t, color="black", linestyle="--", linewidth=0.9, alpha=0.7)
        ax.annotate(
            description, xy=(t, ymax), xytext=(t, ymax * 1.04),
            ha="center", va="bottom", fontsize=8, rotation=0,
        )

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Packets / sec")
    ax.set_title("04_failure_visibility.pcapng — Protocol Isolation During docker stop/start")
    ax.set_xlim(0, duration)
    ax.set_ylim(0, ymax * 1.25)
    ax.legend(loc="upper left")
    ax.grid(axis="y", linestyle="--", alpha=0.3)

    fig.tight_layout()
    out_path = SCRIPT_DIR / "failure_timeline.png"
    fig.savefig(out_path, dpi=200)
    print(f"\nPlot written to: {out_path}")


if __name__ == "__main__":
    main()
