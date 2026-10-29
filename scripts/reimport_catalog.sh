#!/bin/bash
# Reimporta il catalogo dal CSV già presente in data/catalogo_import.csv
# Per aggiornare il CSV: esportare di nuovo dal Google Sheet
# "Catalogo fornitori e prezzi (da Fatture in Cloud)" tab Catalogo
# (solo categoria merce; PORTO e PREVALCART vengono uniti; Santomarco escluso)
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH=.
.venv/bin/python -c "from app.catalog_import import import_catalog; print(import_catalog(username='cli'))"
