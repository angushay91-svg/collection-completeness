"""Acceptance tests for the locked metric definitions (§5.2 / §10).

A tiny hand-constructed fixture: 40 cells across 2 wards with hand-computed
expected values for every metric.
"""
import os
import sys
from datetime import date, timedelta

import h3
import numpy as np
import pandas as pd
import pyproj
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from build import compute_rollup, band_of, display_class  # noqa: E402

BUILD = date(2026, 6, 1)
T = pyproj.Transformer.from_crs(4326, 27700, always_xy=True)
TI = pyproj.Transformer.from_crs(27700, 4326, always_xy=True)


@pytest.fixture()
def fixture():
    # 40 real H3 res-10 cells on a line near Dorchester (valid geometry matters
    # for the UPRN distance test)
    base_lat, base_lon = 50.71, -2.44
    cells_list = []
    seen = set()
    i = 0
    while len(cells_list) < 40:
        c = h3.latlng_to_cell(base_lat, base_lon + i * 0.0025, 10)
        i += 1
        if c not in seen:
            seen.add(c)
            cells_list.append(c)

    rows = []
    for j, c in enumerate(cells_list):
        la, lo = h3.cell_to_latlng(c)
        ward = "W1" if j < 20 else "W2"
        # W1: 10 fresh (incl. one at exactly 364d), 5 @400d, 3 @800d, 2 never
        # W2: 8 fresh, 12 never
        if ward == "W1":
            if j < 10:
                last = BUILD - timedelta(days=364 if j == 0 else 100)
            elif j < 15:
                last = BUILD - timedelta(days=400)
            elif j < 18:
                last = BUILD - timedelta(days=800)
            else:
                last = None
        else:
            last = BUILD - timedelta(days=50) if j < 28 else None
        rows.append({"h3_10": c, "lat": la, "lon": lo, "ward": ward,
                     "ward_nm": ward, "parish": "P_" + ward, "parish_nm": "P_" + ward,
                     "last_dt": last, "total_tests": 10, "passes": 2})
    cells = pd.DataFrame(rows)

    # cell_road: every cell one Unclassified link; cell[0] ALSO touched by an A road (A35)
    cr_rows = [{"h3_10": c, "link": k, "dclass": display_class("Unclassified"),
                "road_number": "", "trunk": False} for k, c in enumerate(cells_list)]
    # A35 over 4 cells: cells 0,1 fresh, cells 18,19 never -> 50% fresh
    for c in [cells_list[0], cells_list[1], cells_list[18], cells_list[19]]:
        cr_rows.append({"h3_10": c, "link": 1000, "dclass": display_class("A Road"),
                        "road_number": "A35", "trunk": True})
    # one Classified Unnumbered link to prove the C mapping
    cr_rows.append({"h3_10": cells_list[2], "link": 2000,
                    "dclass": display_class("Classified Unnumbered"),
                    "road_number": "", "trunk": False})
    cr = pd.DataFrame(cr_rows)

    # UPRNs: one at exactly 249m and one at 251m from a fresh cell centroid;
    # plus one far away (never near anything)
    fresh_cell = cells_list[5]
    fla, flo = h3.cell_to_latlng(fresh_cell)
    fe, fn = T.transform(flo, fla)
    # offset NORTH (perpendicular to the cell line) so only this cell is in range
    uprn = pd.DataFrame({"uprn": [1, 2, 3],
                         "e": [fe, fe, fe + 50000],
                         "n": [fn + 249.0, fn + 251.0, fn]})

    # pins: 3 outstanding (never cells), 1 fresh-hidden, 1 cleared
    pins = pd.DataFrame([
        {"id": "p1", "h3_10": cells_list[18], "lat": 0, "lon": 0, "kind": "map_the_gap",
         "created_at": BUILD - timedelta(days=30)},
        {"id": "p2", "h3_10": cells_list[19], "lat": 0, "lon": 0, "kind": "checker_request",
         "created_at": BUILD - timedelta(days=200)},
        {"id": "p3", "h3_10": cells_list[30], "lat": 0, "lon": 0, "kind": "map_the_gap",
         "created_at": BUILD - timedelta(days=10)},
        # fresh-hidden: cell collected 100d ago, pin created 5d ago (after the pass)
        {"id": "p4", "h3_10": cells_list[3], "lat": 0, "lon": 0, "kind": "checker_request",
         "created_at": BUILD - timedelta(days=5)},
        # cleared: cell refreshed 50d ago, pin created 200d ago (before the pass)
        {"id": "p5", "h3_10": cells_list[25], "lat": 0, "lon": 0, "kind": "map_the_gap",
         "created_at": BUILD - timedelta(days=200)},
    ])
    return cells, cr, uprn, pins


def run(cells, cr, uprn, pins, build=BUILD):
    return compute_rollup(cells, cr, pd.DataFrame(), uprn, pins, build)


def test_headline_and_bands(fixture):
    cells, cr, uprn, pins = fixture
    r, _ = run(cells, cr, uprn, pins)
    c = r["council"]
    assert c["total_cells"] == 40
    assert c["collected_cells"] == 18           # 10 W1 fresh + 8 W2 fresh
    assert c["pct"] == 45.0
    assert c["bands"] == {"0-12": 18, "12-24": 5, ">24": 3, "never": 14}


def test_window_edges_and_falling_headline(fixture):
    cells, cr, uprn, pins = fixture
    # 364 days counts as collected
    assert band_of(BUILD - timedelta(days=364), BUILD) == "0-12"
    # 366 days does not
    assert band_of(BUILD - timedelta(days=366), BUILD) == "12-24"
    r1, _ = run(cells, cr, uprn, pins, BUILD)
    r2, _ = run(cells, cr, uprn, pins, BUILD + timedelta(days=60))
    assert r2["council"]["pct"] < r1["council"]["pct"]   # ages out -> falls
    assert r2["council"]["collected_cells"] == 17        # the 364d cell dropped


def test_class_mapping_and_multiclass_cell(fixture):
    cells, cr, uprn, pins = fixture
    assert display_class("Classified Unnumbered") == "C"
    assert display_class("Some Future Value") == "Residential & minor"
    assert display_class("Motorway") == "Motorway"
    r, _ = run(cells, cr, uprn, pins)
    c = r["council"]
    # cell counted once in headline even though touched by A road + unclassified
    assert c["total_cells"] == 40
    # but appears in both class rows: A class has the 4 A35 cells
    assert c["classes"]["A"]["cells"] == 4
    assert c["classes"]["A"]["pct"] == 50.0              # 2 of 4 fresh
    assert c["classes"]["C"]["cells"] == 1
    assert c["classes"]["Residential & minor"]["cells"] == 40


def test_named_road_table(fixture):
    cells, cr, uprn, pins = fixture
    r, _ = run(cells, cr, uprn, pins)
    a35 = [n for n in r["council"]["named_roads"] if n["road"] == "A35"][0]
    assert a35["cells"] == 4 and a35["pct"] == 50.0 and a35["trunk"] is True


def test_uprn_249_in_251_out(fixture):
    cells, cr, uprn, pins = fixture
    r, _ = run(cells, cr, uprn, pins)
    c = r["council"]
    assert c["uprn_total"] == 3
    assert c["uprn_near"] == 1                  # 249m in, 251m out, 50km out
    assert c["uprn_pct"] == 33.3


def test_demand_statuses_and_fresh_exclusion(fixture):
    cells, cr, uprn, pins = fixture
    r, pins_out = run(cells, cr, uprn, pins)
    assert r["council"]["demand_outstanding"] == 3
    assert r["council"]["demand_cleared"] == 1
    st = dict(zip(pins_out.id, pins_out.status))
    assert st["p4"] == "fresh_hidden"           # never ships in demand.json
    shipped = pins_out[pins_out.status.isin(["outstanding", "cleared"])]
    assert "p4" not in set(shipped.id)


def test_averaging_trap_guard(fixture):
    """Rollups must come from the FULL cell table: creating a filtered view and
    recomputing must give identical numbers for the full-table rollup."""
    cells, cr, uprn, pins = fixture
    r_full, _ = run(cells, cr, uprn, pins)
    _filtered_view = cells[cells.last_dt.notna()]          # someone's filtered export
    r_again, _ = run(cells, cr, uprn, pins)                # full table again
    assert r_full["council"] == r_again["council"]
    # and a rollup computed from the filtered subset is NOT what we publish
    r_bad, _ = run(_filtered_view.reset_index(drop=True), cr, uprn, pins)
    assert r_bad["council"]["pct"] != r_full["council"]["pct"]


def test_ward_rollups_present(fixture):
    cells, cr, uprn, pins = fixture
    r, _ = run(cells, cr, uprn, pins)
    assert set(r["wards"].keys()) == {"W1", "W2"}
    assert r["wards"]["W1"]["total_cells"] == 20
    assert r["wards"]["W1"]["pct"] == 50.0      # 10 of 20
    assert r["wards"]["W2"]["pct"] == 40.0      # 8 of 20
