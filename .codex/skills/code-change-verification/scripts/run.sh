#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT=""

if command -v git >/dev/null 2>&1; then
  REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || true)"
fi

if [[ -z "${REPO_ROOT}" ]]; then
  REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
fi

cd "${REPO_ROOT}"

run_step() {
  echo "Running $*..."
  "$@"
}

run_step make format
run_step make lint
run_step make typecheck
run_step make test
run_step make -C studio/backend format
run_step make -C studio/backend lint
run_step make -C studio/backend typecheck
run_step make -C studio/backend test

echo "code-change-verification: all commands passed."
