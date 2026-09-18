#!/bin/bash
# Launches the native nesting app using the project's venv, regardless of
# what directory this script is invoked from -- lets a .desktop launcher
# (or a plain double-click) start it without a terminal.
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
exec .venv/bin/python native_app/main.py "$@"
