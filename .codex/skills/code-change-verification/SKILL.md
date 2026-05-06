---
name: code-change-verification
description: Run the mandatory Relayna verification stack when changes affect SDK or Studio backend runtime code, tests, packaging, or build/test behavior.
---

# Code Change Verification

## Overview

Use this skill before marking work complete when changes affect Python runtime
code, tests, packaging, or build/test configuration in the Relayna repository.
The goal is to finish with formatting, linting, type checking, and tests passing
for the affected Python workspaces.

You can skip this skill for docs-only or repository metadata changes unless the
user asks for the full verification stack.

## Quick Start

1. Keep this skill at `./.codex/skills/code-change-verification` so it loads with
   the repository.
2. macOS/Linux: run `bash .codex/skills/code-change-verification/scripts/run.sh`.
3. Windows: run `powershell -ExecutionPolicy Bypass -File .codex/skills/code-change-verification/scripts/run.ps1`.
4. If any command fails, fix the issue and rerun the script.
5. Mark work complete only when all required commands succeed.

## Manual Workflow

If dependencies are not installed or dependency files changed, sync first:

```bash
make sync
make -C studio/backend sync
```

Run from the repository root in this order:

```bash
make format
make lint
make typecheck
make test
make -C studio/backend format
make -C studio/backend lint
make -C studio/backend typecheck
make -C studio/backend test
```

Do not skip failing steps. Stop, fix the failure, and rerun the full relevant
stack so the required commands pass in order.

## Scope Guidance

Run the SDK stack for changes under `src/relayna/`, `tests/`, root packaging,
root Makefile targets, or root test/type/lint configuration.

Run the Studio backend stack for changes under `studio/backend/src/`,
`studio/backend/tests/`, `studio/backend/pyproject.toml`, or
`studio/backend/Makefile`.

Run both stacks when a change affects shared contracts, Studio backend behavior
that depends on SDK behavior, root tooling used by Studio, or both workspaces.

## Resources

### `scripts/run.sh`

Executes the full SDK and Studio backend verification sequence with fail-fast
semantics from the repository root.

### `scripts/run.ps1`

Windows-friendly wrapper that runs the same verification sequence with fail-fast
semantics from PowerShell.
