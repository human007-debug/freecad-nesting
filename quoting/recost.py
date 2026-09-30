"""
quoting/recost.py
-----------------
Re-cost a past Excel costing workbook (COSTING SHEET + RAW MATERIAL) with
today's rate card and write it back out in the same costing-sheet layout,
with a COMPARISON sheet against the original.

    python -m quoting.recost OLD_COSTING.xlsx [--card RATE_CARD.xlsx]
                             [--out RECOSTED.xlsx] [--margin PCT | --match-lpr]

--card       the rate card (default: the local one, quoting/rate_card.py
             default_rate_card_path())
--margin     finish with this margin % instead of the workbook's own
--match-lpr  finish with the margin that meets the workbook's LPR
"""

import argparse
import os
import sys

from quoting.costing_sheet_xlsx import write_costing_workbook
from quoting.rate_card import default_rate_card_path, load_rate_card
from quoting.tender_costing import import_costing_workbook


def _fmt(v):
    return "-" if v is None else f"{v:,.2f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m quoting.recost", description=__doc__.split("\n\n")[1])
    ap.add_argument("workbook")
    ap.add_argument("--card", default=None)
    ap.add_argument("--out", default=None)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--margin", type=float, default=None)
    g.add_argument("--match-lpr", action="store_true")
    a = ap.parse_args(argv)

    card_path = a.card or default_rate_card_path()
    if not os.path.exists(card_path):
        print(f"No rate card at {card_path} -- pass --card PATH", file=sys.stderr)
        return 2
    card = load_rate_card(card_path)
    tc, sheet = import_costing_workbook(a.workbook, card)
    if a.margin is not None:
        tc.margin_pct = a.margin
    elif a.match_lpr:
        if not tc.lpr:
            print("The workbook has no LPR to match.", file=sys.stderr)
            return 2
        tc.margin_pct = tc.margin_for_rate(tc.lpr)

    out = a.out or os.path.splitext(a.workbook)[0] + " - recosted.xlsx"
    write_costing_workbook(out, tc, sheet)

    t, orig = tc.totals(), sheet.get("totals", {})
    h = tc.header
    print(f"{h.product.strip()}  ({h.quantity:g} {h.unit}, tender {h.tender_number})")
    print(f"  rate card: {card.summary()}")
    print(f"  BOM: {len(tc.items)} items, gross {tc.gross_weight:,.1f} kg, net {tc.net_weight:,.1f} kg")
    print(f"  {'':24}{'workbook':>16}{'re-costed':>16}")
    for label, key in (("A. material", "material"), ("B. process", "process"), ("C-H. others", "others"),
                       ("I. all total", "all_total"), ("M. all inclusive rate", "all_inclusive")):
        print(f"  {label:24}{_fmt(orig.get(key)):>16}{_fmt(t[key]):>16}")
    print(f"  {tc.margin_label.lower()}: {tc.margin_pct:.1f}%")
    if tc.lpr:
        print(f"  LPR {tc.lpr:,.0f} -> margin to meet it: {tc.margin_for_rate(tc.lpr):.1f}%")
    flagged = [p for p in tc.process_lines() if p.remarks]
    for p in flagged:
        print(f"  note: {p.description.strip()}: {p.remarks}")
    print(f"  written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
