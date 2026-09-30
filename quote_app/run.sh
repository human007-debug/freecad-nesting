#!/bin/bash
# Launches AlphaQuote from any working directory -- lets a .desktop
# launcher (or a plain double-click) start it without a terminal.
# Uses the project's .venv when there is one, else whatever python3 is on PATH.
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
# Under WSL, Qt's Wayland backend can crash with "Protocol error" when panes
# are rearranged; X11 (xcb) is reliable there. An explicit setting wins.
if [ -z "$QT_QPA_PLATFORM" ] && grep -qi microsoft /proc/version 2>/dev/null; then
    export QT_QPA_PLATFORM=xcb
fi
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python quote_app/main.py "$@"
fi
exec python3 quote_app/main.py "$@"
