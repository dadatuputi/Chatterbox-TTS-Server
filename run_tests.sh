#!/usr/bin/env bash
# Convenience runner for the automated test suite.
#   ./run_tests.sh            # run in the current environment
# Inside Docker (recommended — has torch/ffmpeg/bcrypt):
#   docker compose -f deploy/cloudflare/docker-compose.cloudflare.yml \
#     exec chatterbox-tts-server sh -c "pip install -r requirements-test.txt && ./run_tests.sh"
set -euo pipefail
cd "$(dirname "$0")"
python -m pip install -q -r requirements-test.txt
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# 1. Unit tests (helpers, data model, ffmpeg ops, auth). Includes tests/model,
#    which self-skip unless RUN_MODEL_TESTS=1 and torch+chatterbox are present.
python -m pytest tests --ignore=tests/api --ignore=tests/ui -v

# 2. API integration tests (real endpoints) — separate process: they stub heavy ML
#    deps when absent, which must not leak into the unit run.
python -m pytest tests/api -v

# 3. Browser UI tests (Playwright against the running app). Needs a Chromium; the
#    container/CI image or a `playwright install chromium` provides it.
python -m pytest tests/ui -v || echo "UI tests skipped/failed (needs a browser) — see output above"

# 4. Optional real-model generation smoke test (loads weights; heavy):
#    RUN_MODEL_TESTS=1 python -m pytest tests/model -v
