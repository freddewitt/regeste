#!/usr/bin/env bash
# Lance Regeste depuis l'environnement virtuel du projet.
# Usage : ./lancer.sh [--cli] [--lang fr] ...
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/regeste "$@"
