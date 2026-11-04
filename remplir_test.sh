#!/usr/bin/env bash
# Crée une école de TEST complète dans la base de données (ecole.db ou DATABASE_URL du fichier .env).
# Seul le compte directeur est demandé : classes, élèves, professeurs et personnel sont créés automatiquement.
set -e
cd "$(dirname "$0")"
if [ -f .venv/bin/activate ]; then source .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then source .venv/Scripts/activate; fi
PYTHON=python3
command -v python3 >/dev/null 2>&1 || PYTHON=python
"$PYTHON" -m app.cli remplir-test
