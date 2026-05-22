#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m app.workers.poll_chat --interval 5 --page-size 20
