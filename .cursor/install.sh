#!/usr/bin/env bash
# Idempotent Cloud Agent setup for the Inci tennis research bot.
#
# The project is pinned to the exact interpreter CPython 3.14.5 (see
# pyproject.toml and the tennis_v1 AST-boundary suite, whose digest table is
# frozen under CPython 3.14.5). uv installs that interpreter in userspace and
# resolves the fully pinned requirements.txt into a project-local virtualenv.
set -euo pipefail

PYTHON_VERSION="3.14.5"

export PATH="${HOME}/.local/bin:${PATH}"

# uv provides the pinned interpreter and dependency resolver. The installer is
# idempotent, so re-running setup is safe.
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

# No-op once the interpreter is already present in uv's managed store.
uv python install "${PYTHON_VERSION}"

# Recreate the project virtualenv on the pinned interpreter and install the
# exact locked dependencies. Both commands are deterministic re-runs.
uv venv --clear --python "${PYTHON_VERSION}" .venv
uv pip install --python .venv/bin/python -r requirements.txt

echo "Inci environment ready: $(.venv/bin/python --version)"
