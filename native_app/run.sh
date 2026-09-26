#!/bin/bash
# Launches the native nesting app from any working directory -- lets a
# .desktop launcher (or a plain double-click) start it without a terminal.
# Uses the project's .venv when there is one, else whatever python3 is on PATH.
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python native_app/main.py "$@"
fi
exec python3 native_app/main.py "$@"
