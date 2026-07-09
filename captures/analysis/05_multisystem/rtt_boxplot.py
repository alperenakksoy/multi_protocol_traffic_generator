#!/usr/bin/env python3
"""
RTT Boxplot: Single-Machine vs. Multi-Machine
-----------------------------------------------
Extracts tcp.analysis.ack_rtt (Wireshark's per-ACK round-trip-time estimate)
for every TCP-based protocol (HTTP/2, MQTT, Raw TCP) from both the
single-machine and multi-machine captures, and plots them side by side as
boxplots. QUIC is excluded: it runs over UDP, so tcp.analysis.ack_rtt does
not apply to it.

Usage:
    python3 captures/analysis/05_multisystem/rtt_boxplot.py

Requires: tshark (Wireshark), matplotlib, numpy.
Reads:    captures/01_protocol_distribution.pcapng   (single-machine baseline)
          captures/05_multisystem_Mac.fritz.box.pcapng (multi-machine)
Writes:   captures/analysis/05_multisystem/rtt_comparison.png
          captures/analysis/05_multisystem/rtt_summary.txt
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

SINGLE_MACHINE_PCAP = CAPTURES_DIR / "01_protocol_distribution.pcapng"
MULTI_MACHINE_PCAP = CAPTURES_DIR / "05_multisystem_Mac.fritz.box.pcapng"

# (label, Wireshark display filter for "this protocol's TCP port")
PROTOCOLS = [
    ("HTTP/2", "tcp.port==8080"),
    ("MQTT", "tcp.port==1883"),
    ("Raw TCP", "tcp.port==9999"),
]


def find_tshark() -> str:
    found = shutil.which("tshark")
    if found:
        return found
    mac_path = Path("/Applications/Wireshark.app/Contents/MacOS/tshark")
    if mac_path.is_file():
        return str(mac_path)
    sys.exit("ERROR: tshark not found. Install Wireshark or add tshark to PATH.")


def extract_rtt_ms(tshark: str, pcap: Path, port_filter: str) -> np.ndarray:
    """Runs tshark and returns ack_rtt values in milliseconds."""
    if not pcap.is_file():
        sys.exit(f"ERROR: capture file not found: {pcap}")
    cmd = [
        tshark, "-r", str(pcap),
        "-Y", f"{port_filter} and tcp.analysis.ack_rtt",
        "-T", "fields", "-e", "tcp.analysis.ack_rtt",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    values = [float(line) * 1000.0 for line in result.stdout.splitlines() if line.strip()]
    return np.array(values)


def summarize(values: np.ndarray) -> str:
    if values.size == 0:
        return "n=0 (no ACK RTT samples found)"
    return (
        f"n={values.size:>6}  "
        f"median={np.median(values):7.3f} ms  "
        f"mean={np.mean(values):7.3f} ms  "
        f"p95={np.percentile(values, 95):7.3f} ms  "
        f"max={np.max(values):7.3f} ms"
    )


def main() -> None:
    tshark = find_tshark()
    print(f"Using tshark: {tshark}")

    data = {}       # label -> (single_values, multi_values)
    summary_lines = []

    for label, port_filter in PROTOCOLS:
        print(f"Extracting {label} ({port_filter}) ...")
        single_vals = extract_rtt_ms(tshark, SINGLE_MACHINE_PCAP, port_filter)
        multi_vals = extract_rtt_ms(tshark, MULTI_MACHINE_PCAP, port_filter)
        data[label] = (single_vals, multi_vals)

        summary_lines.append(f"{label}:")
        summary_lines.append(f"  single-machine : {summarize(single_vals)}")
        summary_lines.append(f"  multi-machine  : {summarize(multi_vals)}")
        summary_lines.append("")

    summary_text = "\n".join(summary_lines)
    print("\n" + summary_text)

    summary_path = SCRIPT_DIR / "rtt_summary.txt"
    summary_path.write_text(summary_text)
    print(f"Summary written to: {summary_path}")

    # ── Plot: one boxplot pair (single vs. multi) per protocol ─────────────
    fig, ax = plt.subplots(figsize=(8, 5))

    box_data = []
    box_labels = []
    box_colors = []
    positions = []
    pos = 1
    palette = {"single": "#4C72B0", "multi": "#DD8452"}

    for label, _ in PROTOCOLS:
        single_vals, multi_vals = data[label]
        box_data.append(single_vals)
        box_labels.append(f"{label}\nsingle")
        box_colors.append(palette["single"])
        positions.append(pos)
        pos += 1

        box_data.append(multi_vals)
        box_labels.append(f"{label}\nmulti")
        box_colors.append(palette["multi"])
        positions.append(pos)
        pos += 2  # extra gap between protocol groups

    bp = ax.boxplot(
        box_data, positions=positions, widths=1.4, showfliers=True,
        patch_artist=True, medianprops={"color": "black"},
    )
    for patch, color in zip(bp["boxes"], box_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.8)

    ax.set_xticks(positions)
    ax.set_xticklabels(box_labels)
    ax.set_ylabel("TCP ACK RTT (ms)")
    ax.set_yscale("log")
    ax.set_title("Round-Trip Time: Single-Machine vs. Multi-Machine")
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    legend_handles = [
        plt.Rectangle((0, 0), 1, 1, fc=palette["single"], alpha=0.8, label="Single-machine (Docker bridge)"),
        plt.Rectangle((0, 0), 1, 1, fc=palette["multi"], alpha=0.8, label="Multi-machine (real Wi-Fi link)"),
    ]
    ax.legend(handles=legend_handles, loc="upper left")

    fig.tight_layout()
    out_path = SCRIPT_DIR / "rtt_comparison.png"
    fig.savefig(out_path, dpi=200)
    print(f"Plot written to: {out_path}")


if __name__ == "__main__":
    main()
