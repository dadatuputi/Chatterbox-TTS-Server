#!/usr/bin/env bash
# Convenience runner for the automated test suite.
#   ./run_tests.sh            # run in the current environment
# Inside Docker (recommended — has torch/ffmpeg/bcrypt):
#   docker compose -f deploy/cloudflare/docker-compose.cloudflare.yml \
#     exec chatterbox-tts-server sh -c "pip install -r requirements-test.txt && ./run_tests.sh"
set -euo pipefail
cd "$(dirname "$0")"
python -m pip install -q -r requirements-test.txt

# Unit tests (helpers, data model, ffmpeg ops, auth).
python -m pytest tests --ignore=tests/api -v

# API integration tests run in a separate process: they stub heavy ML deps when
# absent, which must not leak into the unit run.
python -m pytest tests/api -v
