"""
quote_app/launch.py
-------------------
Starting one app from the other -- AlphaNest's "Send to AlphaQuote" and
AlphaQuote's "Send to AlphaNest". Qt-free.

Each app runs as its own process, started with the project's .venv Python
when there is one (same rule as the run.sh launchers), else the Python
running now. The environment is inherited, so a QT_QPA_PLATFORM the user
needed for the first app applies to the second too.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _python() -> str:
    for rel in (os.path.join(".venv", "bin", "python"), os.path.join(".venv", "Scripts", "python.exe")):
        candidate = os.path.join(ROOT, rel)
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def _spawn(script: str, args):
    cmd = [_python(), os.path.join(ROOT, script)] + [str(a) for a in args]
    kwargs = {"cwd": ROOT}
    if os.name != "nt":
        kwargs["start_new_session"] = True   # outlives the app that launched it
    return subprocess.Popen(cmd, **kwargs)


def launch_alphaquote(*args):
    """AlphaQuote, e.g. `launch_alphaquote("--open", 12)`."""
    return _spawn(os.path.join("quote_app", "main.py"), args)


def launch_alphanest(parts_json: str):
    """AlphaNest with `parts_json` imported as its job's parts."""
    return _spawn(os.path.join("native_app", "main.py"), [parts_json])
