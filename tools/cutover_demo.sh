#!/usr/bin/env bash
# Live cutover rehearsal for one slice: register -> soak -> promote 0->5->50->100 -> rollback.
# Traffic weights only ever move through tools/cutover.py; this script just drives it and
# records the façade's own access log as evidence.
set -euo pipefail
SLICE=${1:-catalog}
SERVICE=${2:-candidate-catalog}
PORT=${3:-8001}
REQUESTS=${REQUESTS:-40}   # passes over the recorded GETs per soak
SOAK_SECONDS=${SOAK_SECONDS:-5}
FACADE=${FACADE:-http://localhost:8080}
LOG=strangler/logs/access.log

step() { printf '\n== %s ==\n' "$*"; }

backend_mix() {
  # Count backends the façade chose for this slice since the mark.
  tail -n +"$1" "$LOG" | awk -v s="route=$SLICE" '$0 ~ s {for(i=1;i<=NF;i++) if($i ~ /^backend=/) c[$i]++} END {for(k in c) printf "  %s %d\n", k, c[k]}'
}

# Idempotent (GET) requests for this slice from the recorded traffic, as façade-relative URLs.
mapfile -t URLS < <("${PYTHON:-python3}" - "$SLICE" <<'PY'
import sys, yaml
from urllib.parse import urlencode
for r in yaml.safe_load(open("traffic/requests.yaml"))[sys.argv[1]]:
    if r.get("method", "GET").upper() == "GET":
        q = urlencode({k: v for k, v in (r.get("query") or {}).items()})
        print(r["path"] + ("?" + q if q else ""))
PY
)
[ "${#URLS[@]}" -gt 0 ] || { echo "no GET requests recorded for slice $SLICE"; exit 1; }

drive() {
  local mark; mark=$(( $(wc -l < "$LOG") + 1 ))
  for i in $(seq 1 "$REQUESTS"); do
    for url in "${URLS[@]}"; do
      curl -s --max-time 3 -o /dev/null -H "X-Request-Key: key-$i-$RANDOM" "$FACADE$url" || true
    done
  done
  backend_mix "$mark"
}

step "candidate health"
docker compose exec -T legacy sh -c "curl -sf http://$SERVICE:$PORT/healthz"; echo

step "parity gate (fresh measurement against live legacy)"
make -s parity SLICE="$SLICE" | tail -1
"${PYTHON:-python3}" -c "import json;r=json.load(open('parity/$SLICE.json'));print(f\"  match_rate={r['match_rate']} ({r['matched']}/{r['total']})\")"

step "register $SERVICE:$PORT at weight 0 (controller-owned)"
"${PYTHON:-python3}" tools/cutover.py register --slice "$SLICE" --service "$SERVICE" --port "$PORT"

step "soak at weight 0 (mirror only; candidate sees shadow traffic, users see legacy)"
drive

for target in 5 50 100; do
  step "promote -> $target (gated on parity >= 0.99 and candidate 5xx <= legacy 5xx over soak window)"
  "${PYTHON:-python3}" tools/cutover.py promote --slice "$SLICE" --soak-seconds "$SOAK_SECONDS"
  sleep "$SOAK_SECONDS"
  step "soak at weight $target"
  drive
done

step "rollback"
"${PYTHON:-python3}" tools/cutover.py rollback --slice "$SLICE"
drive
grep -A1 "^  $SLICE:" strangler/routes.yaml

if [ "${FAILURE_DRILL:-0}" = "1" ]; then
  step "FAILURE DRILL: promote to 5, then kill the candidate"
  "${PYTHON:-python3}" tools/cutover.py promote --slice "$SLICE" --soak-seconds "$SOAK_SECONDS"
  docker compose stop -t 1 "$SERVICE" >/dev/null
  drive
  step "promote must be refused (candidate 5xx > legacy 5xx)"
  if "${PYTHON:-python3}" tools/cutover.py promote --slice "$SLICE" --soak-seconds 120; then
    echo "  UNEXPECTED: promotion went through"; exit 1
  fi
  step "rollback and restore candidate"
  "${PYTHON:-python3}" tools/cutover.py rollback --slice "$SLICE"
  docker compose start "$SERVICE" >/dev/null
  drive
fi
