#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# test_endpoints.sh
# Tests every curl command used in the Wireshark Test Preparation section.
# Run this AFTER docker-compose up -d and all containers are healthy.
#
# Usage:ooo
#   chmod +x test_endpoints.sh
#   ./test_endpoints.sh
#
# Each test prints PASS or FAIL with the HTTP status code and response.
# ─────────────────────────────────────────────────────────────────────────────

BASE="http://localhost:8000"
PASS=0
FAIL=0

check() {
  local label="$1"
  local expected="$2"
  local actual="$3"
  local body="$4"
  if echo "$actual" | grep -q "$expected"; then
    echo "  PASS  $label"
    PASS=$((PASS+1))
  else
    echo "  FAIL  $label"
    echo "        expected: $expected"
    echo "        got:      $actual"
    echo "        body:     $body"
    FAIL=$((FAIL+1))
  fi
}




docker cp <id>:/tmp/t1_balanced.pcap ./t1_balanced.pcap

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 0 — System health check"
echo "═══════════════════════════════════════════════"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" "$BASE/health")
body=$(cat /tmp/body.txt)
check "GET /health returns 200" "200" "$resp" "$body"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" "$BASE/status")
body=$(cat /tmp/body.txt)
check "GET /status returns 200" "200" "$resp" "$body"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" "$BASE/profiles")
body=$(cat /tmp/body.txt)
check "GET /profiles returns 200" "200" "$resp" "$body"
echo "  profiles available: $body"

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 1 — T1: start with balanced profile"
echo "═══════════════════════════════════════════════"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/start?profile=balanced")
body=$(cat /tmp/body.txt)
check "POST /start?profile=balanced returns 200" "200" "$resp" "$body"

sleep 2
resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" "$BASE/status")
body=$(cat /tmp/body.txt)
check "GET /status shows running=true" "true" "$body" "$body"

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 2 — T1: stop system"
echo "═══════════════════════════════════════════════"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/stop")
body=$(cat /tmp/body.txt)
check "POST /stop returns 200" "200" "$resp" "$body"
sleep 2

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 3 — T2: start with http2_heavy profile"
echo "═══════════════════════════════════════════════"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/start?profile=http2_heavy")
body=$(cat /tmp/body.txt)
check "POST /start?profile=http2_heavy returns 200" "200" "$resp" "$body"
sleep 2

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/stop")
check "POST /stop returns 200" "200" "$resp" "$(cat /tmp/body.txt)"
sleep 2

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 4 — T3: PATCH gen-tcpudp mode=normal"
echo "═══════════════════════════════════════════════"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/start?profile=balanced")
check "POST /start?profile=balanced (for T3)" "200" "$resp" "$(cat /tmp/body.txt)"
sleep 2

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" \
  -X PATCH "$BASE/generator/gen-tcpudp" \
  -H "Content-Type: application/json" \
  -d '{"mode": "normal", "rate": 20}')
body=$(cat /tmp/body.txt)
check "PATCH /generator/gen-tcpudp mode=normal returns 200" "200" "$resp" "$body"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" \
  -X PATCH "$BASE/generator/gen-tcpudp" \
  -H "Content-Type: application/json" \
  -d '{"mode": "stealth"}')
body=$(cat /tmp/body.txt)
check "PATCH /generator/gen-tcpudp mode=stealth returns 200" "200" "$resp" "$body"

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 5 — T3: stop individual generators"
echo "═══════════════════════════════════════════════"

for gen in gen-http2 gen-quic gen-mqtt; do
  resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/generator/$gen/stop")
  body=$(cat /tmp/body.txt)
  check "POST /generator/$gen/stop returns 200" "200" "$resp" "$body"
done

# restart them
for gen in gen-http2 gen-quic gen-mqtt; do
  resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/generator/$gen/start")
  body=$(cat /tmp/body.txt)
  check "POST /generator/$gen/start returns 200" "200" "$resp" "$body"
done

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/stop")
check "POST /stop returns 200" "200" "$resp" "$(cat /tmp/body.txt)"
sleep 2

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 6 — T4: failure visibility setup"
echo "═══════════════════════════════════════════════"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/start?profile=balanced")
check "POST /start?profile=balanced (for T4)" "200" "$resp" "$(cat /tmp/body.txt)"
sleep 2

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/stop")
check "POST /stop returns 200" "200" "$resp" "$(cat /tmp/body.txt)"
sleep 2

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 7 — T6: live pattern switch"
echo "═══════════════════════════════════════════════"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/start?profile=balanced")
check "POST /start?profile=balanced (for T6)" "200" "$resp" "$(cat /tmp/body.txt)"
sleep 2

for pattern in constant periodic_burst ramp random; do
  resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" \
    -X PATCH "$BASE/generator/gen-http2" \
    -H "Content-Type: application/json" \
    -d "{\"pattern\": \"$pattern\"}")
  body=$(cat /tmp/body.txt)
  check "PATCH gen-http2 pattern=$pattern returns 200" "200" "$resp" "$body"
  sleep 1
done

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" -X POST "$BASE/stop")
check "POST /stop final cleanup" "200" "$resp" "$(cat /tmp/body.txt)"

echo ""
echo "═══════════════════════════════════════════════"
echo "  STEP 8 — /config/load endpoint"
echo "═══════════════════════════════════════════════"

resp=$(curl -s -o /tmp/body.txt -w "%{http_code}" \
  -X POST "$BASE/config/load" \
  -H "Content-Type: application/json" \
  -d '{"profile": "balanced"}')
body=$(cat /tmp/body.txt)
check "POST /config/load with balanced returns 200" "200" "$resp" "$body"

echo ""
echo "═══════════════════════════════════════════════"
echo "  RESULTS"
echo "═══════════════════════════════════════════════"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo ""
if [ $FAIL -eq 0 ]; then
  echo "  All endpoints verified — curl commands in the report are accurate."
else
  echo "  Some endpoints failed — review the FAIL lines above."
fi
echo ""
