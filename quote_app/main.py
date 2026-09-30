"""
main.py
-------
AlphaQuote -- the quoting app. Standalone: no FreeCAD, and no AlphaNest
window needed (it shares AlphaNest's engine and part importers, and hands
won quotes to it).

Run with:
    python3 quote_app/main.py [--db PATH] [--open QUOTE_ID]

--db    the quote database (default: see quoting/db.py default_db_path(),
        or the ALPHAQUOTE_DB environment variable)
--open  select that quote on start (AlphaNest's "Send to AlphaQuote" uses it)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # project root

from PySide6 import QtWidgets

from quoting.db import default_db_path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="alphaquote")
    parser.add_argument("--db", default=None)
    parser.add_argument("--open", type=int, default=None, dest="open_id")
    opts, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    from quote_app.main_window import QuoteAppWindow

    app = QtWidgets.QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("AlphaQuote")
    win = QuoteAppWindow(opts.db or default_db_path())
    win.resize(1360, 860)
    win.show()
    if opts.open_id is not None:
        win.open_quote(opts.open_id)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
