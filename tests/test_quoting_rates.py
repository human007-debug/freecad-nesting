"""Phase 2: rate tables -- lookup, interpolation, storage round trips."""

import pytest

from quoting.rates import (CALIBRATE_ME, AssemblyRate, BendRate, CuttingRate, MachiningRate,
                           RateLookupError, RateTable, SawRate, WeldRate, load_rates,
                           load_rates_sqlite, save_rates, save_rates_sqlite, starter_rates)


def small_table():
    return RateTable([
        CuttingRate("mild steel", 2.0, 8000, 0.4, 100.0, 10.0, True),
        CuttingRate("mild steel", 4.0, 4000, 0.8, 120.0, 12.0, True),
        BendRate("*", 1.0, 10, 15, 5, 60.0),
        BendRate("*", 5.0, 20, 25, 5, 60.0),
        SawRate(50, 30, 10, 60),
        SawRate(100, 60, 10, 60),
    ])


def test_exact_lookup_normalises_material():
    row = small_table().lookup(CuttingRate, "Steel", 2.0)
    assert row.speed_mm_min == 8000


def test_interpolates_between_thicknesses():
    row = small_table().lookup(CuttingRate, "mild steel", 3.0)
    assert row.thickness == 3.0
    assert row.speed_mm_min == pytest.approx(6000)
    assert row.pierce_s == pytest.approx(0.6)
    assert row.rate_per_hr == pytest.approx(110)
    assert row.calibrated is True


def test_refuses_to_extrapolate():
    with pytest.raises(RateLookupError, match="outside the table"):
        small_table().lookup(CuttingRate, "mild steel", 6.0)


def test_unknown_material_without_wildcard_raises():
    with pytest.raises(RateLookupError):
        small_table().lookup(CuttingRate, "titanium", 2.0)


def test_wildcard_rows_are_the_fallback():
    row = small_table().lookup(BendRate, "stainless", 3.0)
    assert row.hit_s == pytest.approx(15)


def test_band_lookup_picks_smallest_covering_band():
    t = small_table()
    assert t.lookup(SawRate, None, 40).cut_s == 30
    assert t.lookup(SawRate, None, 50).cut_s == 30
    assert t.lookup(SawRate, None, 51).cut_s == 60
    with pytest.raises(RateLookupError, match="above the largest band"):
        t.lookup(SawRate, None, 150)


def test_starter_rates_cover_the_three_materials_and_are_marked():
    t = starter_rates()
    for material in ("mild steel", "stainless", "aluminium"):
        row = t.lookup(CuttingRate, material, 3.0)
        assert row.speed_mm_min > 0
    assert all(not r.calibrated and r.note == CALIBRATE_ME for r in t.all_rows())
    assert len(t.uncalibrated_rows()) == len(t.all_rows())
    # every built-in operation can find something
    t.lookup(BendRate, "aluminium", 2.0)
    t.lookup(WeldRate, "fillet", 4.0)
    t.lookup(MachiningRate, "tapped", 6.8)
    t.lookup(MachiningRate, "pocket", 0)
    t.lookup(AssemblyRate, "anything")


def test_thicker_is_slower_in_starter_rates():
    t = starter_rates()
    speeds = [t.lookup(CuttingRate, "mild steel", th).speed_mm_min for th in (1, 2, 3, 5, 8, 10)]
    assert speeds == sorted(speeds, reverse=True)


def test_sqlite_round_trip(tmp_path):
    t = starter_rates()
    path = str(tmp_path / "rates.db")
    save_rates_sqlite(path, t)
    back = load_rates_sqlite(path)
    assert back.all_rows() == t.all_rows()
    # saving again replaces, not appends
    save_rates(path, small_table())
    assert len(load_rates(path).all_rows()) == 6


def test_xlsx_round_trip(tmp_path):
    pytest.importorskip("openpyxl")
    t = starter_rates()
    path = str(tmp_path / "rates.xlsx")
    save_rates(path, t)
    back = load_rates(path)
    assert back.all_rows() == t.all_rows()


def test_xlsx_hand_edited_sheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CuttingRate"
    ws.append(["Note", "Material", "Thickness", "Speed_mm_min", "Pierce_s", "Rate per hr", "Calibrated"])
    ws.append(["measured 2026-09", "Mild Steel", 3, "5200", 0.5, 110, "yes"])
    ws.append([None, None, None, None, None, None, None])
    wb.create_sheet("My notes").append(["ignored"])
    path = str(tmp_path / "shop.xlsx")
    wb.save(path)
    t = load_rates(path)
    (row,) = t.all_rows()
    assert row == CuttingRate("Mild Steel", 3.0, 5200.0, 0.5, 110.0, 10.0, True, "measured 2026-09")


def test_xlsx_missing_required_value_names_the_row(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SawRate"
    ws.append(["max_section", "cut_s", "setup_min", "rate_per_hr"])
    ws.append([50, None, 10, 60])
    path = str(tmp_path / "bad.xlsx")
    wb.save(path)
    with pytest.raises(ValueError, match="row 2"):
        load_rates(path)


def test_add_rejects_non_rate_rows():
    with pytest.raises(TypeError):
        RateTable().add(object())
