#!/usr/bin/env python3
"""
I/O Graph: Burst / Constant / Ramp Patterns (Capture 02)
-----------------------------------------------------------
Recreates Wireshark's "Statistics -> I/O Graph" for 02_temporal_analysis.pcapng
as an annotated matplotlib figure: HTTP/2 (tcp.port==8080) and QUIC
(udp.port==4433) packets/sec over the full capture, with phase boundaries
and detected burst peaks marked. MQTT and raw TCP are not plotted - this
capture only merges the analyzer-http2 and analyzer-quic vantage points
(see capture_02_temporal_analysis.sh), so those two lines are flat zero.

Phase layout (from capture_02_temporal_analysis.sh / config/http2_heavy.yaml,
capture starts right after warmup so phase boundaries are relative to t=0):
    Phase 1  0-120s   http2_burst     - periodic bursts every 30s
    Phase 2  120-180s http2_dominant  - constant rate
    Phase 3  180-240s http2_ramp      - linear rate ramp (shows up as a
                                        discrete step-staircase because
                                        Hypercorn's keep_alive_max_requests
                                        =1000 forces periodic connection
                                        cycling - see report Section VII-B)

Usage:
    python3 captures/analysis/02_temporal/burst_ramp_io_graph.py

Requires: tshark (Wireshark), matplotlib, numpy.
Reads:    captures/02_temporal_analysis.pcapng
Writes:   captures/analysis/02_temporal/burst_ramp_io_graph.png
          captures/analysis/02_temporal/burst_ramp_summary.txt
"""

import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
PCAP = REPO_ROOT / "captures" / "02_temporal_analysis.pcapng"

BIN_SECONDS = 1.0
PHASES = [
    ("Burst", 0, 120, "#f0f0f0"),
    ("Constant", 120, 180, "#e2e2e2"),
    ("Ramp", 180, 240, "#f0f0f0"),
]

SERIES = [
    ("HTTP/2", "tcp.port==8080", "#C44E52"),
    ("QUIC", "udp.port==4433", "#4C72B0"),
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


def bin_per_second(timestamps: np.ndarray, duration: float) -> tuple[np.ndarray, np.ndarray]:
    n_bins = int(np.ceil(duration / BIN_SECONDS))
    edges = np.arange(0, n_bins + 1) * BIN_SECONDS
    counts, _ = np.histogram(timestamps, bins=edges)
    centers = edges[:-1] + BIN_SECONDS / 2
    return centers, counts


def find_peaks(y: np.ndarray, min_height: float, min_distance: int) -> list[int]:
    """Simple local-maxima peak finder (no scipy dependency)."""
    peaks: list[int] = []
    for i in range(1, len(y) - 1):
        if y[i] >= y[i - 1] and y[i] >= y[i + 1] and y[i] >= min_height:
            if not peaks or i - peaks[-1] >= min_distance:
                peaks.append(i)
            elif y[i] > y[peaks[-1]]:
                peaks[-1] = i
    return peaks


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

    binned = {}
    for label, _, _ in SERIES:
        centers, counts = bin_per_second(all_ts[label], duration)
        binned[label] = (centers, counts)

    # ── Detect burst peaks in the HTTP/2 line within the Burst phase ───────
    http2_centers, http2_counts = binned["HTTP/2"]
    phase1_mask = (http2_centers >= PHASES[0][1]) & (http2_centers < PHASES[0][2])
    phase1_counts = http2_counts[phase1_mask]
    phase1_centers = http2_centers[phase1_mask]
    baseline = np.median(phase1_counts)
    peak_idx = find_peaks(phase1_counts, min_height=baseline * 1.8, min_distance=10)
    peak_times = phase1_centers[peak_idx]
    peak_values = phase1_counts[peak_idx]

    summary_lines = [
        f"Capture duration: {duration:.1f}s",
        f"HTTP/2 packets: {all_ts['HTTP/2'].size}   QUIC packets: {all_ts['QUIC'].size}",
        "",
        f"Burst phase (0-120s) baseline (median pkt/s): {baseline:.1f}",
        f"Detected burst peaks (t, pkt/s): "
        + ", ".join(f"({t:.0f}s, {v:.0f})" for t, v in zip(peak_times, peak_values)),
        "",
        f"Constant phase (120-180s) mean pkt/s: {http2_counts[(http2_centers>=120)&(http2_centers<180)].mean():.1f}",
        f"Ramp phase (180-240s) mean pkt/s: {http2_counts[(http2_centers>=180)&(http2_centers<240)].mean():.1f}",
    ]
    summary_text = "\n".join(summary_lines)
    print("\n" + summary_text)
    (SCRIPT_DIR / "burst_ramp_summary.txt").write_text(summary_text + "\n")

    # ── Plot ─────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(11, 5))

    for name, start, end, color in PHASES:
        ax.axvspan(start, end, color=color, zorder=0)
        ax.text((start + end) / 2, ax.get_ylim()[1], name, ha="center", va="bottom")

    for label, _, color in SERIES:
        centers, counts = binned[label]
        ax.plot(centers, counts, label=label, color=color, linewidth=1.2)

    ax.scatter(peak_times, peak_values, color="black", zorder=5, s=25, marker="v")
    for t, v in zip(peak_times, peak_values):
        ax.annotate(
            "burst", (t, v), textcoords="offset points", xytext=(0, 8),
            ha="center", fontsize=8,
        )

    for _, start, _, _ in PHASES[1:]:
        ax.axvline(start, color="gray", linestyle="--", linewidth=0.8)

    ax.annotate(
        "discrete step-ramp\n(Hypercorn connection\ncycling artifact,\nnot generator ramp logic)",
        xy=(210, http2_counts[(http2_centers >= 200) & (http2_centers < 220)].mean()),
        xytext=(200, ax.get_ylim()[1] * 0 + max(http2_counts) * 0.55),
        fontsize=8, ha="center",
        arrowprops=dict(arrowstyle="->", color="black", lw=0.8),
    )

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Packets / sec")
    ax.set_title("02_temporal_analysis.pcapng — HTTP/2 vs. QUIC over time (http2_heavy profile)")
    ax.set_xlim(0, duration)
    ax.legend(loc="upper right")
    ax.grid(axis="y", linestyle="--", alpha=0.3)

    fig.tight_layout()
    out_path = SCRIPT_DIR / "burst_ramp_io_graph.png"
    fig.savefig(out_path, dpi=200)
    print(f"\nPlot written to: {out_path}")


if __name__ == "__main__":
    main()
