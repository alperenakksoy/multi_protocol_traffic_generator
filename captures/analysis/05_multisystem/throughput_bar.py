#!/usr/bin/env python3
"""
Throughput Bar Chart: HTTP/2 vs. MQTT, Single-Machine vs. Multi-Machine
-----------------------------------------------------------------------
Counts completed application-level operations per second for both
protocols in both captures, and plots requests/sec (HTTP/2) and
messages/sec (MQTT) as grouped bars. Meant to sit next to
rtt_comparison.png on the same slide: "same RTT floor for both protocols,
but only HTTP/2's synchronous request loop turns that latency into a
throughput collapse - MQTT's async publish barely notices."

HTTP/2: counts request headers (http2.headers.method). A single captured
frame can contain multiple coalesced requests (e.g. tshark returns
"GET,GET,POST" for one frame) - more likely under real network latency.
Naively counting matching frames would undercount; this script splits on
commas to count actual request headers.

MQTT: counts PUBLISH packets (mqtt.msgtype==3) travelling TO the broker
(tcp.dstport==1883) only. The generator self-subscribes to its own topics
(see docs/ARCHITECTURE.md), so the broker fans every PUBLISH straight back
out to it - counting mqtt.msgtype==3 in both directions would silently
double the rate. Filtering by destination port keeps only the genuine
generator -> broker publish, matching what the generator actually sent.

Usage:
    python3 captures/analysis/05_multisystem/throughput_bar.py

Requires: tshark (Wireshark), matplotlib.
Reads:    captures/01_protocol_distribution.pcapng   (single-machine baseline)
          captures/05_multisystem_Mac.fritz.box.pcapng (multi-machine)
Writes:   captures/analysis/05_multisystem/throughput_comparison.png
          captures/analysis/05_multisystem/throughput_summary.txt
"""

import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
CAPTURES_DIR = REPO_ROOT / "captures"

CAPTURES = [
    ("Single-machine", CAPTURES_DIR / "01_protocol_distribution.pcapng", 69.9),
    ("Multi-machine", CAPTURES_DIR / "05_multisystem_Mac.fritz.box.pcapng", 70.0),
]


def find_tshark() -> str:
    found = shutil.which("tshark")
    if found:
        return found
    mac_path = Path("/Applications/Wireshark.app/Contents/MacOS/tshark")
    if mac_path.is_file():
        return str(mac_path)
    sys.exit("ERROR: tshark not found. Install Wireshark or add tshark to PATH.")


def run_tshark_fields(tshark: str, pcap: Path, display_filter: str, field: str) -> list[str]:
    if not pcap.is_file():
        sys.exit(f"ERROR: capture file not found: {pcap}")
    cmd = [tshark, "-r", str(pcap), "-Y", display_filter, "-T", "fields", "-e", field]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def count_http2_requests(tshark: str, pcap: Path) -> int:
    """Counts HTTP/2 request headers, splitting coalesced frames on ','."""
    lines = run_tshark_fields(tshark, pcap, "http2.headers.method", "http2.headers.method")
    return sum(len([tok for tok in line.split(",") if tok.strip()]) for line in lines)


def count_mqtt_publishes(tshark: str, pcap: Path) -> int:
    """Counts genuine client -> broker PUBLISH packets (excludes self-sub fan-out)."""
    lines = run_tshark_fields(
        tshark, pcap, "mqtt.msgtype==3 and tcp.dstport==1883", "mqtt.msgtype"
    )
    return len([line for line in lines if line.strip()])


PROTOCOLS = [
    ("HTTP/2", "requests", count_http2_requests),
    ("MQTT", "messages", count_mqtt_publishes),
]


def main() -> None:
    tshark = find_tshark()
    print(f"Using tshark: {tshark}")

    results = {}  # label -> {"single": (n, rate), "multi": (n, rate)}
    summary_lines = []

    for proto_label, unit, count_fn in PROTOCOLS:
        rates = {}
        for cap_label, pcap, duration_s in CAPTURES:
            print(f"Counting {proto_label} {unit} in {pcap.name} ...")
            n = count_fn(tshark, pcap)
            rate = n / duration_s
            rates[cap_label] = (n, rate)
            line = f"{proto_label} / {cap_label}: {n} {unit} / {duration_s}s = {rate:.1f} {unit}/s"
            print("  " + line)
            summary_lines.append(line)
        results[proto_label] = rates

        single_rate = rates["Single-machine"][1]
        multi_rate = rates["Multi-machine"][1]
        drop_pct = (single_rate - multi_rate) / single_rate * 100
        summary_lines.append(
            f"{proto_label} throughput change: {single_rate:.1f} -> {multi_rate:.1f} {unit}/s "
            f"({'-' if drop_pct >= 0 else '+'}{abs(drop_pct):.1f}%)\n"
        )
        print(summary_lines[-1])

    summary_path = SCRIPT_DIR / "throughput_summary.txt"
    summary_path.write_text("\n".join(summary_lines) + "\n")
    print(f"Summary written to: {summary_path}")

    # ── Plot: one single/multi bar pair per protocol ────────────────────
    fig, ax = plt.subplots(figsize=(6.5, 5))
    palette = {"Single-machine": "#4C72B0", "Multi-machine": "#DD8452"}

    positions, heights, colors, bar_labels, annotations = [], [], [], [], []
    pos = 1
    for proto_label, unit, _ in PROTOCOLS:
        for cap_label, _, _ in CAPTURES:
            n, rate = results[proto_label][cap_label]
            positions.append(pos)
            heights.append(rate)
            colors.append(palette[cap_label])
            annotations.append(f"{rate:.1f}/s\n(n={n})")
            pos += 1
        pos += 1  # gap between protocol groups

    bars = ax.bar(positions, heights, color=colors, alpha=0.85, width=0.8)
    for bar, text in zip(bars, annotations):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height() + max(heights) * 0.02,
            text, ha="center", va="bottom", fontsize=9,
        )

    group_centers = [1.5, 5.5]
    ax.set_xticks(group_centers)
    ax.set_xticklabels([p[0] for p in PROTOCOLS], fontsize=11)

    legend_handles = [
        plt.Rectangle((0, 0), 1, 1, fc=palette["Single-machine"], alpha=0.85, label="Single-machine (Docker bridge)"),
        plt.Rectangle((0, 0), 1, 1, fc=palette["Multi-machine"], alpha=0.85, label="Multi-machine (real Wi-Fi link)"),
    ]
    ax.legend(handles=legend_handles, loc="upper right")

    ax.set_ylabel("Throughput (ops / sec)")
    ax.set_title("Application-Level Throughput: HTTP/2 vs. MQTT")
    ax.set_ylim(0, max(heights) * 1.3)
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    fig.tight_layout()
    out_path = SCRIPT_DIR / "throughput_comparison.png"
    fig.savefig(out_path, dpi=200)
    print(f"Plot written to: {out_path}")


if __name__ == "__main__":
    main()
