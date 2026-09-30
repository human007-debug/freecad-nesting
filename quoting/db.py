"""
quoting/db.py
-------------
AlphaQuote's store (docs/QUOTING_PLAN.md, Phase 4): customers and quotes in
one SQLite file. Plain Python, stdlib `sqlite3` only.

A QUOTE is one row per revision: quotes sharing a `number` ("Q-2026-0007")
are revisions of the same enquiry, `revision` counting up from 1. Each row
carries its status (draft / sent / won / lost), its customer, a title, the
editor's content as JSON (quote_model.quote_to_dict) and the last priced
summary (quote_model.result_summary), so the quote list can show values
without re-pricing anything. A won quote also records which quantity was
won -- that's the quantity AlphaNest nests.

Revising copies a quote into a new draft row with the next revision
number; the old revision is left as it was (what the customer was sent).
"""

import datetime
import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional

STATUSES = ("draft", "sent", "won", "lost")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    contact TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    won_quantity INTEGER,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE (number, revision)
);
CREATE INDEX IF NOT EXISTS quotes_number ON quotes(number);
"""


def _now() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")


@dataclass
class Customer:
    name: str
    contact: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    notes: str = ""
    id: Optional[int] = None


@dataclass
class QuoteRecord:
    number: str
    revision: int = 1
    customer_id: Optional[int] = None
    title: str = ""
    status: str = "draft"
    won_quantity: Optional[int] = None
    created: str = ""
    updated: str = ""
    data: dict = field(default_factory=dict)
    summary: list = field(default_factory=list)
    notes: str = ""
    id: Optional[int] = None
    customer_name: Optional[str] = None     # filled in by queries, not stored

    @property
    def label(self) -> str:
        return f"{self.number} rev {self.revision}"

    def price_at(self, index: int = 0) -> Optional[float]:
        return self.summary[index]["price"] if len(self.summary) > index else None


class QuoteDB:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        with self.con:
            self.con.executescript(_SCHEMA)

    def close(self):
        self.con.close()

    # ------------------------------------------------------------ customers

    def add_customer(self, customer: Customer) -> Customer:
        name = customer.name.strip()
        if not name:
            raise ValueError("a customer needs a name")
        with self.con:
            cur = self.con.execute(
                "INSERT INTO customers (name, contact, email, phone, address, notes) VALUES (?,?,?,?,?,?)",
                (name, customer.contact, customer.email, customer.phone, customer.address, customer.notes))
        customer.name, customer.id = name, cur.lastrowid
        return customer

    def update_customer(self, customer: Customer):
        with self.con:
            self.con.execute(
                "UPDATE customers SET name=?, contact=?, email=?, phone=?, address=?, notes=? WHERE id=?",
                (customer.name.strip(), customer.contact, customer.email, customer.phone, customer.address,
                 customer.notes, customer.id))

    def delete_customer(self, customer_id: int):
        with self.con:
            self.con.execute("DELETE FROM customers WHERE id=?", (customer_id,))

    def customers(self) -> List[Customer]:
        rows = self.con.execute("SELECT * FROM customers ORDER BY name COLLATE NOCASE")
        return [Customer(**dict(r)) for r in rows]

    def customer(self, customer_id: int) -> Optional[Customer]:
        r = self.con.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        return Customer(**dict(r)) if r else None

    def customer_by_name(self, name: str) -> Optional[Customer]:
        r = self.con.execute("SELECT * FROM customers WHERE name=? COLLATE NOCASE", (name.strip(),)).fetchone()
        return Customer(**dict(r)) if r else None

    def get_or_add_customer(self, name: str) -> Customer:
        return self.customer_by_name(name) or self.add_customer(Customer(name))

    # --------------------------------------------------------------- quotes

    def next_number(self, today: Optional[datetime.date] = None) -> str:
        year = (today or datetime.date.today()).year
        prefix = f"Q-{year}-"
        rows = self.con.execute("SELECT number FROM quotes WHERE number LIKE ?", (prefix + "%",))
        seq = [int(r["number"][len(prefix):]) for r in rows if r["number"][len(prefix):].isdigit()]
        return f"{prefix}{(max(seq) if seq else 0) + 1:04d}"

    def new_quote(self, title: str = "", customer_id: Optional[int] = None, data: Optional[dict] = None,
                  number: Optional[str] = None) -> QuoteRecord:
        now = _now()
        rec = QuoteRecord(number=number or self.next_number(), title=title, customer_id=customer_id,
                          created=now, updated=now, data=data or {})
        with self.con:
            cur = self.con.execute(
                "INSERT INTO quotes (number, revision, customer_id, title, status, created, updated, data, "
                "summary, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rec.number, rec.revision, rec.customer_id, rec.title, rec.status, rec.created, rec.updated,
                 json.dumps(rec.data), json.dumps(rec.summary), rec.notes))
        rec.id = cur.lastrowid
        return self.quote(rec.id)

    def save_quote(self, rec: QuoteRecord) -> QuoteRecord:
        if rec.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {rec.status!r}")
        rec.updated = _now()
        with self.con:
            self.con.execute(
                "UPDATE quotes SET customer_id=?, title=?, status=?, won_quantity=?, updated=?, data=?, "
                "summary=?, notes=? WHERE id=?",
                (rec.customer_id, rec.title, rec.status, rec.won_quantity, rec.updated, json.dumps(rec.data),
                 json.dumps(rec.summary), rec.notes, rec.id))
        return rec

    def set_status(self, quote_id: int, status: str, won_quantity: Optional[int] = None):
        rec = self.quote(quote_id)
        rec.status = status
        rec.won_quantity = won_quantity if status == "won" else None
        self.save_quote(rec)

    def revise(self, quote_id: int) -> QuoteRecord:
        """A new draft revision of `quote_id`'s enquiry, copied from it."""
        src = self.quote(quote_id)
        top = self.con.execute("SELECT MAX(revision) FROM quotes WHERE number=?", (src.number,)).fetchone()[0]
        now = _now()
        with self.con:
            cur = self.con.execute(
                "INSERT INTO quotes (number, revision, customer_id, title, status, created, updated, data, "
                "summary, notes) VALUES (?,?,?,?,'draft',?,?,?,?,?)",
                (src.number, top + 1, src.customer_id, src.title, now, now, json.dumps(src.data),
                 json.dumps(src.summary), src.notes))
        return self.quote(cur.lastrowid)

    def duplicate(self, quote_id: int) -> QuoteRecord:
        """A brand-new enquiry (new number) with `quote_id`'s content."""
        src = self.quote(quote_id)
        rec = self.new_quote(title=f"{src.title} (copy)" if src.title else "", customer_id=src.customer_id,
                             data=src.data)
        rec.summary = src.summary
        return self.save_quote(rec)

    def delete_quote(self, quote_id: int):
        with self.con:
            self.con.execute("DELETE FROM quotes WHERE id=?", (quote_id,))

    def _record(self, row) -> QuoteRecord:
        d = dict(row)
        d["data"] = json.loads(d["data"] or "{}")
        d["summary"] = json.loads(d["summary"] or "[]")
        return QuoteRecord(**d)

    _SELECT = ("SELECT q.*, c.name AS customer_name FROM quotes q "
               "LEFT JOIN customers c ON c.id = q.customer_id")

    def quote(self, quote_id: int) -> Optional[QuoteRecord]:
        r = self.con.execute(self._SELECT + " WHERE q.id=?", (quote_id,)).fetchone()
        return self._record(r) if r else None

    def revisions(self, number: str) -> List[QuoteRecord]:
        rows = self.con.execute(self._SELECT + " WHERE q.number=? ORDER BY q.revision", (number,))
        return [self._record(r) for r in rows]

    def quotes(self, search: str = "", status: Optional[str] = None, customer_id: Optional[int] = None,
               latest_only: bool = False) -> List[QuoteRecord]:
        """Newest first. `search` matches number, title or customer name."""
        where, args = [], []
        if search.strip():
            like = f"%{search.strip()}%"
            where.append("(q.number LIKE ? OR q.title LIKE ? OR c.name LIKE ?)")
            args += [like, like, like]
        if status:
            where.append("q.status = ?")
            args.append(status)
        if customer_id is not None:
            where.append("q.customer_id = ?")
            args.append(customer_id)
        if latest_only:
            where.append("q.revision = (SELECT MAX(revision) FROM quotes q2 WHERE q2.number = q.number)")
        sql = self._SELECT + (" WHERE " + " AND ".join(where) if where else "") + \
            " ORDER BY q.number DESC, q.revision DESC"
        return [self._record(r) for r in self.con.execute(sql, args)]


def default_db_path() -> str:
    """ALPHAQUOTE_DB if set, else a per-user file: ~/.local/share/AlphaQuote
    (Linux), %APPDATA%\\AlphaQuote (Windows), ~/Library/Application
    Support/AlphaQuote (macOS)."""
    env = os.environ.get("ALPHAQUOTE_DB")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif os.uname().sysname == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "AlphaQuote", "quotes.db")
