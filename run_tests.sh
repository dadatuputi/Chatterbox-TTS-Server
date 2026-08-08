#!/usr/bin/env bash
# Convenience runner for the automated test suite.
#   ./run_tests.sh            # run in the current environment
# Inside Docker (recommended — has torch/ffmpeg/bcrypt):
#   docker compose -f deploy/cloudflare/docker-compose.cloudflare.yml \
#     exec chatterbox-tts-server sh -c "pip install -r requirements-test.txt && ./run_tests.sh"
set -euo pipefail
cd "$(dirname "$0")"
python -m pip install -q -r requirements-test.txt
exec python -m pytest tests/ -v
