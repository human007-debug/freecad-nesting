"""bar_nest.py: 1D bar nesting -- known best packings, kerf and trim."""

import pytest

from bar_nest import BarStock, nest_bars


def stock(length, qty=None, **kw):
    return BarStock("mild steel", "RHS 100x50x3", length, quantity=qty, **kw)


def n_bars(res):
    return len(res.bars)


def test_exact_fit_without_kerf():
    res = nest_bars([("a", 2000, 3)], [stock(6000)])
    assert n_bars(res) == 1 and res.bars[0].free == pytest.approx(0)
    assert res.utilisation() == pytest.approx(1.0)


def test_kerf_is_charged_per_piece():
    # 3 x 2000 + 3 x 3 kerf = 6009 > 6000: only two fit on the first bar.
    res = nest_bars([("a", 2000, 3)], [stock(6000)], kerf=3)
    assert n_bars(res) == 2
    assert sorted(len(b.pieces) for b in res.bars) == [1, 2]


def test_trim_reduces_usable_length():
    # 2 x 2995 = 5990: fits 6000 - 10 trimmed, not 6000 - 10 - 10.
    res = nest_bars([("a", 2995, 2)], [stock(6000)], kerf=0, trim_start=10, trim_end=10)
    assert n_bars(res) == 2
    res2 = nest_bars([("a", 2995, 2)], [stock(6000)], kerf=0, trim_start=10, trim_end=0)
    assert n_bars(res2) == 1


def test_offcut_and_remnants():
    res = nest_bars([("a", 2500, 1), ("b", 1000, 1)], [stock(6000)], kerf=5, trim_start=20,
                    min_remnant=1000)
    (bar,) = res.bars
    assert bar.used == pytest.approx(2505 + 1005)
    assert bar.offcut == pytest.approx(6000 - 20 - 3510)
    (rem,) = res.remnants
    assert rem.is_remnant and rem.length == pytest.approx(bar.offcut) and rem.quantity == 1


def test_short_offcut_is_not_a_remnant():
    res = nest_bars([("a", 5500, 1)], [stock(6000)], min_remnant=1000)
    assert res.remnants == []


def test_ffd_miss_fixed_by_local_improvement():
    """5,5,4,4,3,3,3,3 into bars of 10: FFD opens 4 bars, optimum is 3
    ((5,5), (4,3,3), (4,3,3))."""
    pieces = [("p5", 500, 2), ("p4", 400, 2), ("p3", 300, 4)]
    plain = nest_bars(pieces, [stock(1000)], improve=False)
    assert n_bars(plain) == 4
    res = nest_bars(pieces, [stock(1000)])
    assert n_bars(res) == 3
    assert all(b.free == pytest.approx(0) for b in res.bars)
    assert sorted(n for b in res.bars for n, _ in b.pieces) == sorted(
        ["p5"] * 2 + ["p4"] * 2 + ["p3"] * 4)


def test_known_optimum_frame_cut_list():
    # A frame: 4 x 1800 + 4 x 1150 + 2 x 600 from 6 m bars, 3 mm kerf.
    # Total 13000 + 30 kerf -> at least 3 bars, and 3 is achievable:
    # (1800,1800,1150,1150), (1800,1800,1150,1150), (600,600).
    res = nest_bars([("long", 1800, 4), ("short", 1150, 4), ("stub", 600, 2)], [stock(6000)], kerf=3)
    assert n_bars(res) == 3
    assert not res.unplaced


def test_downsizes_to_shorter_stock():
    res = nest_bars([("a", 1500, 1)], [stock(6000), stock(2000)])
    assert res.bars[0].stock.length == 2000


def test_remnants_used_first_and_quantity_respected():
    rem = stock(2500, qty=1, is_remnant=True)
    res = nest_bars([("a", 2000, 2)], [stock(6000), rem])
    lengths = sorted(b.stock.length for b in res.bars)
    assert lengths == [2500, 6000]
    assert rem.quantity == 1                      # input not mutated


def test_finite_stock_leaves_pieces_unplaced():
    res = nest_bars([("a", 2000, 4)], [stock(6000, qty=1)])
    assert n_bars(res) == 1 and res.unplaced == ["a"]


def test_piece_longer_than_any_stock_is_unplaced():
    res = nest_bars([("huge", 7000, 1), ("ok", 1000, 1)], [stock(6000)])
    assert res.unplaced == ["huge"] and n_bars(res) == 1


def test_invalid_piece_length():
    with pytest.raises(ValueError):
        nest_bars([("bad", 0, 1)], [stock(6000)])


def test_bar_pricing_per_m_and_per_kg():
    assert stock(6000, price_per_m=12.0).material_cost() == pytest.approx(72.0)
    s = stock(6000, price_per_kg=1.5, kg_per_m=6.78)
    assert s.material_cost(1000) == pytest.approx(10.17)
    assert s.weight_kg() == pytest.approx(40.68)
    assert stock(6000).material_cost() is None
