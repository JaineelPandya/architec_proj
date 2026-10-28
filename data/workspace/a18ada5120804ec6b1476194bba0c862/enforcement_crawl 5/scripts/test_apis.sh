#!/usr/bin/env bash
#
# scripts/test_apis.sh
# =====================
# Smoke-tests every module's health check + one live endpoint call
# (SAT, NSE, SEBI, BSE). Starts a local uvicorn server automatically if one
# isn't already listening on HOST:PORT, and stops it on exit if it did.
#
# Usage:
#   ./scripts/test_apis.sh
#   PORT=8080 SERVICE_TOKEN=mytoken ./scripts/test_apis.sh
#
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8001}"
BASE="http://${HOST}:${PORT}/api/enforcement_crawler"
TOKEN="${SERVICE_TOKEN:-test_token}"
AUTH_HEADER="Authorization: Bearer ${TOKEN}"

TMP_DIR="$(mktemp -d)"
RESP_FILE="${TMP_DIR}/resp.json"
SERVER_LOG="${TMP_DIR}/server.log"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    echo "Stopping server we started (pid $SERVER_PID)..."
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

is_up() {
  curl -s -o /dev/null --max-time 2 "${BASE}/health"
}

if ! is_up; then
  echo "No server detected at ${BASE} — starting one from ${ROOT_DIR}..."
  # `exec` replaces the subshell with uvicorn itself, so $! below is uvicorn's
  # real PID — without it, `kill $SERVER_PID` only kills the subshell wrapper
  # and leaves the actual server process running as an orphan.
  (cd "$ROOT_DIR" && exec python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" > "$SERVER_LOG" 2>&1) &
  SERVER_PID=$!

  for _ in $(seq 1 30); do
    is_up && break
    sleep 0.5
  done

  if ! is_up; then
    echo "Server failed to start. Last log lines:"
    tail -n 40 "$SERVER_LOG"
    exit 1
  fi
  echo "Server is up (pid $SERVER_PID)."
else
  echo "Using already-running server at ${BASE}."
fi

PASS=0
FAIL=0

check() {
  local name="$1" method="$2" url="$3" data="${4:-}"
  local status
  if [[ -n "$data" ]]; then
    status=$(curl -s -o "$RESP_FILE" -w "%{http_code}" -X "$method" "$url" \
      -H "$AUTH_HEADER" -H "Content-Type: application/json" -d "$data" --max-time 90)
  else
    status=$(curl -s -o "$RESP_FILE" -w "%{http_code}" -X "$method" "$url" \
      -H "$AUTH_HEADER" --max-time 30)
  fi

  if [[ "$status" == "200" ]]; then
    local count
    count=$(python3 -c "import json,sys
try:
    d = json.load(open('$RESP_FILE'))
    print(d.get('count', d.get('status', '')))
except Exception:
    print('?')")
    echo "  OK   ${name}  (200, count/status=${count})"
    PASS=$((PASS + 1))
  else
    echo "  FAIL ${name}  (${status})"
    head -c 300 "$RESP_FILE"
    echo
    FAIL=$((FAIL + 1))
  fi
}

echo
echo "== Health =="
check "root"  GET  "${BASE}/health"
check "sat"   GET  "${BASE}/sat/health"
check "nse"   GET  "${BASE}/nse/health"
check "sebi"  GET  "${BASE}/sebi/health"
check "bse"   GET  "${BASE}/bse/health"

echo
echo "== SAT =="
check "orders/date-wise" POST "${BASE}/sat/orders/date-wise" \
  '{"apl_type": "sebi", "from_date": "01-06-2026", "to_date": "30-06-2026"}'

echo
echo "== NSE =="
check "circulars" POST "${BASE}/nse/circulars" \
  '{"from_date": "01-06-2026", "to_date": "30-06-2026"}'

echo
echo "== SEBI =="
check "circular" POST "${BASE}/sebi/circular" \
  '{"from_date": "01-06-2026", "to_date": "30-06-2026"}'
check "master-circular" POST "${BASE}/sebi/master-circular" \
  '{"from_date": "01-01-2020", "to_date": "30-06-2026", "department": ""}'

echo
echo "== BSE =="
check "circular" POST "${BASE}/bse/circular" \
  '{"start_date": "01-03-2026", "end_date": "31-03-2026", "segment": "", "category": "", "department": ""}'

echo
echo "== Logs =="
check "view-logs" GET "${BASE}/view-logs?lines=10"

echo
echo "Passed: ${PASS}  Failed: ${FAIL}"
[[ "$FAIL" -eq 0 ]]
