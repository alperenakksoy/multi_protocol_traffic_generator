"""
Metrics Collector
-----------------
Receives stats from all generators via POST /update.
Exposes aggregated stats via GET /metrics.
"""

from flask import Flask, request, jsonify
import threading, time

app = Flask(__name__)
lock = threading.Lock()

store = {
    "generators": {},   # name → latest stat snapshot
    "start_time": time.time(),
}


# ── Receive stats from a generator ────────────────────────────────────────────

@app.route("/update", methods=["POST"])
def update():
    payload = request.get_json(silent=True)
    if not payload or "generator" not in payload:
        return jsonify({"error": "missing 'generator' field"}), 400

    name = payload["generator"]
    with lock:
        store["generators"][name] = {**payload, "_ts": time.time()}

    return jsonify({"ok": True})


# ── Aggregated metrics endpoint ────────────────────────────────────────────────

@app.route("/metrics", methods=["GET"])
def metrics():
    with lock:
        gens = dict(store["generators"])

    total_packets = sum(g.get("packets_sent", 0) for g in gens.values())
    total_bytes   = sum(g.get("bytes_sent",   0) for g in gens.values())
    total_errors  = sum(g.get("errors",        0) for g in gens.values())

    # bytes per second over the last 5 seconds (rough estimate)
    rate_bps = sum(g.get("rate_bps", 0) for g in gens.values())

    return jsonify({
        "uptime_seconds": int(time.time() - store["start_time"]),
        "total_packets":  total_packets,
        "total_bytes":    total_bytes,
        "total_errors":   total_errors,
        "rate_bps":       rate_bps,
        "generators":     gens,
    })


# ── Reset counters ─────────────────────────────────────────────────────────────

@app.route("/reset", methods=["POST"])
def reset():
    with lock:
        for name in store["generators"]:
            store["generators"][name]["packets_sent"] = 0
            store["generators"][name]["bytes_sent"]   = 0
            store["generators"][name]["errors"]        = 0
        store["start_time"] = time.time()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9090, debug=False)
