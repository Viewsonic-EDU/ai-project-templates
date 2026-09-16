#!/usr/bin/env bash
# Self-contained; Python is test-only. Production cell() remains pure awk.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 -u "$ROOT/tests/tickets_width_test.py" "$@"
