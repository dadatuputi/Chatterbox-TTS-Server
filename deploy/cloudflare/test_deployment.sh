#!/usr/bin/env bash
# Smoke test for the Cloudflare Tunnel + Access stack.
# Run ON THE SERVER, from deploy/cloudflare/, after creating and editing .env.
#
#   ./test_deployment.sh          # check an already-running stack
#   ./test_deployment.sh --up     # build + start first, then check
#
# Read-only; never tears anything down. Non-zero exit if any check fails.

set -uo pipefail
cd "$(dirname "$0")"

DO_UP=0
for a in "$@"; do case "$a" in --up) DO_UP=1 ;; *) echo "unknown arg: $a"; exit 2 ;; esac; done

PASS=0; FAIL=0
ok()  { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
info(){ echo "        $1"; }
hdr() { echo; echo "== $1 =="; }
COMPOSE="docker compose -f docker-compose.cloudflare.yml"

hdr "Configuration"
if [[ ! -f .env ]]; then bad ".env missing — cp .env.example .env and fill it in"; echo; echo "RESULT: 0 pass, 1 fail"; exit 1; fi
set -a; source .env; set +a
[[ -n "${TUNNEL_TOKEN:-}" ]] && ok "TUNNEL_TOKEN is set" || bad "TUNNEL_TOKEN is empty"
[[ -n "${APP_HOSTNAME:-}" ]] && ok "APP_HOSTNAME=${APP_HOSTNAME}" || bad "APP_HOSTNAME is empty"

hdr "Prerequisites"
command -v docker >/dev/null && ok "docker present" || bad "docker not found"
$COMPOSE version >/dev/null 2>&1 && ok "docker compose present" || bad "docker compose not found"
command -v curl >/dev/null && ok "curl present" || bad "curl not found"

hdr "Compose"
$COMPOSE config >/dev/null 2>&1 && ok "compose file valid" || bad "docker compose config failed"

if [[ $DO_UP -eq 1 ]]; then
  hdr "Bringing up the stack (build)"
  $COMPOSE up -d --build || bad "compose up failed"
  info "waiting ~180s for the app to load the model and the tunnel to register..."
  for _ in $(seq 1 36); do sleep 5; done
fi

hdr "Containers"
for svc in cloudflared chatterbox-tts-server; do
  cid=$($COMPOSE ps -q "$svc" 2>/dev/null)
  if [[ -n "$cid" && "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" == "true" ]]; then ok "$svc running"; else bad "$svc not running"; fi
done

hdr "Tunnel connection"
if $COMPOSE logs --tail=200 cloudflared 2>/dev/null | grep -qi "Registered tunnel connection"; then
  ok "cloudflared registered a tunnel connection"
else
  bad "no 'Registered tunnel connection' in cloudflared logs yet"
  info "check: $COMPOSE logs cloudflared"
fi

APP="https://${APP_HOSTNAME:-chatter.example.com}"

hdr "Edge gating (unauthenticated should NOT get 200)"
code=$(curl -sS --max-time 20 -o /dev/null -w '%{http_code}' "$APP/" || echo 000)
if [[ "$code" == "302" || "$code" == "303" || "$code" == "403" ]]; then ok "UI gated by Access (unauth -> $code)"
elif [[ "$code" == "200" ]]; then bad "UI returned 200 without login — Access is NOT gating this hostname"
else info "UI returned $code (tunnel/DNS may still be propagating)"; fi

if [[ -n "${CF_ACCESS_CLIENT_ID:-}" && -n "${CF_ACCESS_CLIENT_SECRET:-}" ]]; then
  hdr "API via service token"
  code=$(curl -sS --max-time 20 -o /dev/null -w '%{http_code}' \
    -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" \
    -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" \
    "$APP/v1/audio/voices" || echo 000)
  [[ "$code" == "200" ]] && ok "/v1 with service token -> 200" || bad "/v1 with service token -> $code (expected 200)"
  code=$(curl -sS --max-time 20 -o /dev/null -w '%{http_code}' "$APP/v1/audio/voices" || echo 000)
  [[ "$code" != "200" ]] && ok "/v1 without token -> $code (gated)" || bad "/v1 reachable without token"
else
  info "Set CF_ACCESS_CLIENT_ID/SECRET in .env to also test the /v1 API path."
fi

echo
echo "RESULT: $PASS pass, $FAIL fail"
echo
echo "Manual checks (browser):"
echo "  1. Open $APP -> Cloudflare login -> land in the UI."
echo "  2. As an admin: Voice Cloning -> Import from URL/file -> Preview + Import works."
echo "  3. As a friend (non-admin): the /import_reference_url path shows the Access denial."
echo "  4. Generate speech and confirm audio plays."
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
