#!/usr/bin/env bash
# Launch Regeste from the project virtual environment.
# Double-click this file to start the GUI.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/regeste "$@"
