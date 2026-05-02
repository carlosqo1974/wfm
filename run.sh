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

python3 app.py
