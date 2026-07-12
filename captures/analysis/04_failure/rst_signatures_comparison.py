#!/usr/bin/env python3
"""
Three RST Signatures: docker stop vs. iptables REJECT vs. real-network jitter
--------------------------------------------------------------------------------
"TCP RST" is not one signal - it has (at least) three distinct root causes,
each with its own signature. This figure puts all three side by side, each
backed by a real capture:

  1. docker stop target-tcpudp (04_failure_visibility.pcapng)
     -> 0 RST in the entire capture. The container's entire network
        namespace is torn down, so there's no kernel left to answer with
        anything. Chosen over the target-http2 variant (04b) deliberately:
        04b has an unrelated 7-packet RST cluster ~4s before its own stop
        command (Hypercorn's own keep_alive_max_requests=1000 connection
        cycling, see capture 02), which muddies the "clean before/after"
        story. target-tcpudp has no such confound anywhere in the file.

  2. iptables REJECT --reject-with tcp-reset (04c_failure_visibility_reject.pcapng)
     -> RST immediately, every time, for as long as the rule is active.
        Container and kernel stay fully alive; the port is just actively
        refused from outside - the textbook "connection refused" case.

  3. Multi-machine real Wi-Fi link, normal operation (05_multisystem_*.pcapng)
     -> RST occasionally (~3.7% of connections), with no deliberate trigger
        at all. A connection already closed cleanly (FIN/FIN-ACK), but a
        late/duplicate ACK - a real artifact of Wi-Fi loss/retransmission -
        arrives afterwards for a connection the kernel no longer tracks,
        so it replies RST. Never observed on the single-machine Docker
        bridge, where nothing is lossy or duplicated.

Usage:
    python3 captures/analysis/04_failure/rst_signatures_comparison.py

Requires: tshark (Wireshark), matplotlib, numpy.
Reads:    captures/04_failure_visibility.pcapng
          captures/04c_failure_visibility_reject.pcapng
          captures/05_multisystem_Mac.fritz.box.pcapng
Writes:   captures/analysis/04_failure/rst_signatures_comparison.png
          captures/analysis/04_failure/rst_signatures_summary.txt
"""

import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
CAPTURES_DIR = REPO_ROOT / "captures"

BIN_SECONDS = 1.0


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
    cmd = [tshark, "-r", str(pcap), "-Y", display_filter, "-T", "fields", "-e", "frame.time_relative"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return np.array([float(line) for line in result.stdout.splitlines() if line.strip()])


def bin_per_second(timestamps: np.ndarray, duration: float) -> tuple[np.ndarray, np.ndarray]:
    n_bins = max(int(np.ceil(duration / BIN_SECONDS)), 1)
    edges = np.arange(0, n_bins + 1) * BIN_SECONDS
    counts, _ = np.histogram(timestamps, bins=edges)
    centers = edges[:-1] + BIN_SECONDS / 2
    return centers, counts


def get_capture_duration(tshark: str, pcap: Path) -> float:
    """True recording length, independent of when the last matching packet
    for any one filter happened to occur - so a panel whose traffic goes
    silent early (e.g. after docker stop) still shows the full silence."""
    cmd = [tshark, "-r", str(pcap), "-T", "fields", "-e", "frame.time_relative"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    last = 0.0
    for line in result.stdout.splitlines():
        if line.strip():
            last = float(line)
    return last


PANELS = [
    {
        "title": "1. docker stop target-tcpudp",
        "pcap": CAPTURES_DIR / "04_failure_visibility.pcapng",
        "traffic_filter": "tcp.port==9999 or udp.port==9999",
        "traffic_label": "Raw TCP/UDP pkt/s",
        "traffic_color": "#8172B2",
        "events": [(30, "docker stop")],
        "note": "0 RST in the entire capture\n(clean cliff at the stop,\nno pre-existing artifact)",
        "duration_override": 50,
    },
    {
        "title": "2. iptables REJECT --reject-with tcp-reset",
        "pcap": CAPTURES_DIR / "04c_failure_visibility_reject.pcapng",
        "traffic_filter": "tcp.port==8080",
        "traffic_label": "HTTP/2 pkt/s",
        "traffic_color": "#C44E52",
        "events": [(10, "REJECT inserted"), (40, "REJECT removed")],
        "note": None,  # filled in from measured RST count
    },
    {
        "title": "3. Multi-machine, real Wi-Fi link (no deliberate trigger)",
        "pcap": CAPTURES_DIR / "05_multisystem_Mac.fritz.box.pcapng",
        "traffic_filter": "tcp.port==9999",
        "traffic_label": "Raw TCP pkt/s",
        "traffic_color": "#8172B2",
        "events": [],
        "note": None,  # filled in from measured RST count
    },
]


def main() -> None:
    tshark = find_tshark()
    print(f"Using tshark: {tshark}")

    summary_lines = []
    fig, axes = plt.subplots(3, 1, figsize=(10, 10))

    for ax, panel in zip(axes, PANELS):
        pcap = panel["pcap"]
        print(f"\n{panel['title']}  ({pcap.name})")

        traffic_ts = extract_timestamps(tshark, pcap, panel["traffic_filter"])
        rst_ts = extract_timestamps(tshark, pcap, "tcp.flags.reset==1")
        full_duration = float(np.ceil(get_capture_duration(tshark, pcap)))
        duration = min(full_duration, panel.get("duration_override", full_duration))

        # Crop to the displayed window - RST markers outside it would be
        # misleading (implies relevance to what's shown) and the extra
        # flat/empty tail past the interesting part adds nothing.
        traffic_ts = traffic_ts[traffic_ts <= duration]
        rst_ts = rst_ts[rst_ts <= duration]

        centers, counts = bin_per_second(traffic_ts, duration)
        ax.plot(centers, counts, color=panel["traffic_color"], linewidth=1.1, label=panel["traffic_label"])

        ymax = max(counts.max(), 1)
        if rst_ts.size:
            legend_suffix = panel.get("rst_legend_suffix", "")
            ax.scatter(rst_ts, np.full_like(rst_ts, ymax * 0.06), color="black", marker="|",
                       s=200, linewidths=1.2, label=f"RST (n={rst_ts.size}{legend_suffix})", zorder=5)

        for t, label in panel["events"]:
            ax.axvline(t, color="gray", linestyle="--", linewidth=0.9)
            ax.annotate(label, xy=(t, ymax), xytext=(t, ymax * 1.05), ha="center", fontsize=8)

        note = panel["note"] or f"{rst_ts.size} RST packet(s) total"
        ax.text(0.99, 0.95, note, transform=ax.transAxes, ha="right", va="top", fontsize=8,
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.85, edgecolor="lightgray"))

        ax.set_title(panel["title"], fontsize=10, loc="left")
        ax.set_ylabel(panel["traffic_label"])
        ax.set_xlim(0, duration)
        ax.set_ylim(0, ymax * 1.25)
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(axis="y", linestyle="--", alpha=0.3)

        line = f"{panel['title']}: {rst_ts.size} RST packets over {duration:.0f}s"
        print("  " + line)
        summary_lines.append(line)
        if rst_ts.size:
            summary_lines.append(f"  RST timestamps: {', '.join(f'{t:.2f}s' for t in rst_ts[:10])}"
                                  + (" ..." if rst_ts.size > 10 else ""))

    axes[-1].set_xlabel("Time (s)")
    fig.suptitle("Three Root Causes for the Same Wire Signal: TCP RST", fontsize=13, y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    out_path = SCRIPT_DIR / "rst_signatures_comparison.png"
    fig.savefig(out_path, dpi=200)
    print(f"\nPlot written to: {out_path}")

    summary_path = SCRIPT_DIR / "rst_signatures_summary.txt"
    summary_path.write_text("\n".join(summary_lines) + "\n")
    print(f"Summary written to: {summary_path}")


if __name__ == "__main__":
    main()
