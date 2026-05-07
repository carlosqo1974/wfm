#!/usr/bin/env bash
# WFM Contact Center — script de inicio
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$(pwd)"

PORT="${WFM_PORT:-5000}"
echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   WFM — Workforce Management Contact Center     ║"
echo "  ║   http://localhost:${PORT}                          ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""

PYTHON="python3"
if [ -f "venv/bin/python3" ]; then
  PYTHON="venv/bin/python3"
fi

$PYTHON app.py
