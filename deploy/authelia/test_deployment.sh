#!/usr/bin/env bash
# Smoke test for the Authelia + Caddy + Chatterbox-TTS-Server stack.
# Run this ON THE SERVER, from deploy/authelia/, after creating and editing .env.
#
#   ./test_deployment.sh            # run checks against an already-running stack
#   ./test_deployment.sh --up       # build + start the stack first, then check
#   ./test_deployment.sh --insecure # accept self-signed / internal-CA TLS (curl -k)
#
# It never edits your files and never tears anything down. Exit code is non-zero
# if any check fails, so an agent can gate on it.

set -uo pipefail
cd "$(dirname "$0")"

CURL_OPTS=(-sS --max-time 20)
DO_UP=0
for a in "$@"; do
  case "$a" in
    --up) DO_UP=1 ;;
    --insecure) CURL_OPTS+=(-k) ;;
    *) echo "unknown arg: $a" ; exit 2 ;;
  esac
done

PASS=0; FAIL=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
info() { echo "        $1"; }
hdr()  { echo; echo "== $1 =="; }

# --- load .env -------------------------------------------------------------
hdr "Configuration"
if [[ ! -f .env ]]; then
  bad ".env missing — cp .env.example .env and fill it in"; echo; echo "RESULT: 0 pass, 1 fail"; exit 1
fi
set -a; # shellcheck disable=SC1091
source .env; set +a
for v in APP_DOMAIN AUTH_DOMAIN COOKIE_DOMAIN API_TOKEN AUTHELIA_SESSION_SECRET AUTHELIA_STORAGE_ENCRYPTION_KEY AUTHELIA_JWT_SECRET; do
  val="${!v:-}"
  if [[ -z "$val" || "$val" == change-me* ]]; then bad "$v is unset or still a placeholder"; else ok "$v is set"; fi
done
[[ "${APP_DOMAIN:-}" == *.* ]] && ok "APP_DOMAIN looks like a hostname ($APP_DOMAIN)" || bad "APP_DOMAIN not a hostname"

# --- prerequisites ---------------------------------------------------------
hdr "Prerequisites"
command -v docker >/dev/null && ok "docker present" || bad "docker not found"
docker compose version >/dev/null 2>&1 && ok "docker compose present" || bad "docker compose not found"
if docker info 2>/dev/null | grep -qi nvidia; then ok "NVIDIA container runtime detected"
else info "NVIDIA runtime not detected — fine for the CPU image, required for GPU"; fi

# users_database must not contain the placeholder hash
if grep -q "REPLACE_WITH_GENERATED_HASH" authelia/users_database.yml 2>/dev/null; then
  bad "authelia/users_database.yml still has the placeholder password hash"
else ok "users_database.yml has a real password hash"; fi

# domains in the Authelia config should match .env
if grep -q "tts.example.com\|auth.example.com" authelia/configuration.yml; then
  bad "authelia/configuration.yml still has example.com domains — edit to match .env"
else ok "authelia/configuration.yml domains edited"; fi

# --- compose validity ------------------------------------------------------
hdr "Compose"
if docker compose -f docker-compose.authelia.yml config >/dev/null 2>&1; then ok "compose file valid"
else bad "docker compose config failed"; fi

if [[ $DO_UP -eq 1 ]]; then
  hdr "Bringing up the stack (build)"
  docker compose -f docker-compose.authelia.yml up -d --build || bad "compose up failed"
  info "waiting up to 180s for the app to load the model..."
  for _ in $(seq 1 36); do sleep 5; done
fi

hdr "Containers"
for svc in caddy authelia redis chatterbox-tts-server; do
  state=$(docker compose -f docker-compose.authelia.yml ps -q "$svc" 2>/dev/null)
  if [[ -n "$state" ]] && [[ "$(docker inspect -f '{{.State.Running}}' "$state" 2>/dev/null)" == "true" ]]; then
    ok "$svc running"
  else bad "$svc not running"; fi
done

# --- HTTP behaviour --------------------------------------------------------
APP="https://${APP_DOMAIN:-tts.example.com}"
AUTH="https://${AUTH_DOMAIN:-auth.example.com}"

hdr "Authelia portal"
code=$(curl "${CURL_OPTS[@]}" -o /dev/null -w '%{http_code}' "$AUTH/" || echo 000)
[[ "$code" == "200" ]] && ok "portal reachable at $AUTH ($code)" || bad "portal not reachable at $AUTH (got $code)"

hdr "Web UI is protected (unauthenticated should NOT get 200)"
code=$(curl "${CURL_OPTS[@]}" -o /dev/null -w '%{http_code}' "$APP/" || echo 000)
if [[ "$code" == "302" || "$code" == "303" || "$code" == "401" ]]; then ok "UI gated (unauth -> $code)"
elif [[ "$code" == "200" ]]; then bad "UI returned 200 without login — auth NOT enforced!"
else info "UI returned $code (check the stack is up and DNS resolves)"; fi

hdr "Machine API bearer token"
code=$(curl "${CURL_OPTS[@]}" -o /dev/null -w '%{http_code}' "$APP/v1/audio/voices" || echo 000)
[[ "$code" == "401" ]] && ok "API without token -> 401" || bad "API without token -> $code (expected 401)"
code=$(curl "${CURL_OPTS[@]}" -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${API_TOKEN:-}" "$APP/v1/audio/voices" || echo 000)
[[ "$code" == "200" ]] && ok "API with token -> 200" || bad "API with token -> $code (expected 200)"

# --- summary ---------------------------------------------------------------
echo
echo "RESULT: $PASS pass, $FAIL fail"
echo
echo "Manual checks (need a browser — a session cookie can't be scripted here):"
echo "  1. Open $APP -> you are redirected to $AUTH, log in, land back in the UI."
echo "  2. As an 'admins' user: Voice Cloning -> Import from URL/file -> Preview + Import works."
echo "  3. As a 'users' (friend) account: the import/upload actions return 403."
echo "  4. Generate speech in the UI and confirm audio plays."
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
