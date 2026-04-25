#!/usr/bin/env bash
set -euo pipefail

python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m playwright install chromium
sudo .venv/bin/python -m playwright install-deps chromium

if [ -n "${PYTR_KEYS_B64:-}" ]; then
  echo "$PYTR_KEYS_B64" | base64 -d | tar -xzf - -C "$HOME"
  echo "Restaurado ~/.pytr/ desde PYTR_KEYS_B64"
fi

if [ -n "${GSPREAD_AUTH_B64:-}" ]; then
  mkdir -p "$HOME/.config"
  echo "$GSPREAD_AUTH_B64" | base64 -d | tar -xzf - -C "$HOME/.config"
  echo "Restaurado ~/.config/gspread/ desde GSPREAD_AUTH_B64"
fi

echo ""
echo "Entorno listo. Ejecuta: make all"
