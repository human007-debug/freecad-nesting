"""quoting/db.py and quoting/quote_model.py's save/load: AlphaQuote's store."""

import datetime
import math

import pytest

from quoting.costing import QuoteEngine
from quoting.db import Customer, QuoteDB, default_db_path
from quoting.quote_model import (LineSpec, QuoteSetup, build_engine, nesting_parts_json, quote_from_dict,
                                 quote_to_dict, result_summary)
from quoting.rates import starter_rates


@pytest.fixture
def db(tmp_path):
    d = QuoteDB(str(tmp_path / "sub" / "quotes.db"))
    yield d
    d.close()


# ------------------------------------------------------------- customers

def test_customers_crud_and_unique_names(db):
    acme = db.add_customer(Customer("  Acme Fab ", email="buy@acme.test"))
    assert acme.id and acme.name == "Acme Fab"
    db.add_customer(Customer("Bolt & Co"))
    assert [c.name for c in db.customers()] == ["Acme Fab", "Bolt & Co"]
    with pytest.raises(Exception):
        db.add_customer(Customer("Acme Fab"))
    with pytest.raises(ValueError):
        db.add_customer(Customer("   "))
    acme.phone = "123"
    db.update_customer(acme)
    assert db.customer(acme.id).phone == "123"
    assert db.get_or_add_customer("acme fab").id == acme.id       # case-insensitive
    assert db.get_or_add_customer("New Co").id not in (acme.id, None)


def test_deleting_a_customer_keeps_their_quotes(db):
    c = db.add_customer(Customer("Gone Ltd"))
    q = db.new_quote("bracket", customer_id=c.id)
    assert db.quote(q.id).customer_name == "Gone Ltd"
    db.delete_customer(c.id)
    q = db.quote(q.id)
    assert q is not None and q.customer_id is None and q.customer_name is None


# ----------------------------------------------------------------- quotes

def test_numbers_count_up_per_year(db):
    assert db.next_number(datetime.date(2026, 5, 1)) == "Q-2026-0001"
    a = db.new_quote(number=db.next_number(datetime.date(2026, 5, 1)))
    b = db.new_quote(number=db.next_number(datetime.date(2026, 5, 2)))
    assert (a.number, b.number) == ("Q-2026-0001", "Q-2026-0002")
    assert db.next_number(datetime.date(2027, 1, 1)) == "Q-2027-0001"


def test_save_and_reload_data_and_summary(db):
    q = db.new_quote("frame")
    q.data = {"lines": [{"name": "x"}]}
    q.summary = [{"sets": 1, "cost": 10.0, "price": 12.5, "set_price": 12.5, "complete": True}]
    q.notes = "call back Friday"
    db.save_quote(q)
    back = db.quote(q.id)
    assert back.data == q.data and back.summary == q.summary and back.notes == "call back Friday"
    assert back.price_at(0) == 12.5 and back.price_at(3) is None
    q.status = "maybe"
    with pytest.raises(ValueError):
        db.save_quote(q)


def test_revise_copies_and_keeps_the_original(db):
    q = db.new_quote("frame", data={"v": 1})
    db.set_status(q.id, "sent")
    r2 = db.revise(q.id)
    assert (r2.number, r2.revision, r2.status, r2.data) == (q.number, 2, "draft", {"v": 1})
    r2.data = {"v": 2}
    db.save_quote(r2)
    assert db.quote(q.id).data == {"v": 1} and db.quote(q.id).status == "sent"
    r3 = db.revise(q.id)                           # revising an old one still takes the next number
    assert r3.revision == 3
    assert [r.revision for r in db.revisions(q.number)] == [1, 2, 3]


def test_duplicate_is_a_new_enquiry(db):
    q = db.new_quote("frame", data={"v": 1})
    d = db.duplicate(q.id)
    assert d.number != q.number and d.revision == 1 and d.title == "frame (copy)" and d.data == {"v": 1}


def test_won_records_the_quantity(db):
    q = db.new_quote()
    db.set_status(q.id, "won", won_quantity=10)
    assert (db.quote(q.id).status, db.quote(q.id).won_quantity) == ("won", 10)
    db.set_status(q.id, "lost", won_quantity=10)
    assert db.quote(q.id).won_quantity is None


def test_list_search_filter_and_latest(db):
    acme = db.add_customer(Customer("Acme"))
    a = db.new_quote("Guard frame", customer_id=acme.id)
    db.new_quote("Bracket")
    db.revise(a.id)
    db.set_status(a.id, "sent")
    assert len(db.quotes()) == 3
    assert [(q.title, q.revision) for q in db.quotes(latest_only=True)] == [("Bracket", 1), ("Guard frame", 2)]
    assert {q.title for q in db.quotes(search="acme")} == {"Guard frame"}
    assert {q.title for q in db.quotes(search="brack")} == {"Bracket"}
    assert [q.revision for q in db.quotes(status="sent")] == [1]
    assert len(db.quotes(customer_id=acme.id)) == 2
    db.delete_quote(a.id)
    assert len(db.quotes()) == 2


def test_default_db_path_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHAQUOTE_DB", str(tmp_path / "x.db"))
    assert default_db_path() == str(tmp_path / "x.db")
    monkeypatch.delenv("ALPHAQUOTE_DB")
    assert default_db_path().endswith("quotes.db") and "AlphaQuote" in default_db_path()


# --------------------------------------------------------- quote model

def _circle(cx, cy, r, n=24):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _lines():
    return [
        LineSpec("Plate", "sheet", quantity=2, material="mild steel", thickness=3.0, bends=1,
                 outer=[(0, 0), (100, 0), (100, 50), (0, 50)], holes=[_circle(50, 25, 5)], paint=True),
        LineSpec("Rail", "tube", material="mild steel", length=900.0),
        LineSpec("Bolt", "purchased", quantity=4, unit_cost=0.2, markup_pct=50.0),
    ]


def test_quote_dict_round_trip_is_plain_json():
    import json
    setup = QuoteSetup(breaks=[1, 25], markups={"material": 5.0}, transport=0.0)
    overrides = {(25, "Plate", "laser"): 1.5}
    d = json.loads(json.dumps(quote_to_dict(_lines(), setup, overrides)))
    lines, setup2, overrides2 = quote_from_dict(d)
    assert lines == _lines() and setup2 == setup and overrides2 == overrides
    assert quote_from_dict({}) == ([], QuoteSetup(), {})


def test_unknown_keys_are_ignored_for_forward_compatibility():
    d = quote_to_dict(_lines(), QuoteSetup())
    d["lines"][0]["colour"] = "red"
    d["setup"]["future_option"] = 1
    lines, setup, _ = quote_from_dict(d)
    assert lines[0].name == "Plate"


def test_nesting_parts_json_has_sets_worth_of_sheet_parts():
    data = nesting_parts_json(_lines(), sets=10)
    (part,) = data["parts"]
    assert part["name"] == "Plate" and part["quantity"] == 20
    assert part["thickness"] == 3.0 and part["material"] == "mild steel" and len(part["holes"]) == 1
    # and part_import.py / the app's JSON loader can read it
    from part_import import load_parts
    import json, tempfile, os
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.json")
        with open(path, "w") as f:
            json.dump(data, f)
        (p,) = load_parts(path)
    assert p.quantity == 20 and p.thickness == 3.0


def test_result_summary():
    engine, features, _ = build_engine(_lines(), QuoteSetup(breaks=[1, 5]), starter_rates(), None)
    assert isinstance(engine, QuoteEngine)
    rows = result_summary(engine.price(features, breaks=[1, 5]))
    assert [r["sets"] for r in rows] == [1, 5]
    assert rows[1]["set_price"] < rows[0]["set_price"]
    assert set(rows[0]) == {"sets", "cost", "price", "set_price", "complete"}
