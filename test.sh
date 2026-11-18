#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# uv manages the interpreter; there are no third-party host Python dependencies.
uv run --no-project python -B -m unittest discover -s tests
uv run --no-project python -B -m unittest test_port_reconciler
for suite in image/runtime/test-*.sh; do bash "$suite"; done
