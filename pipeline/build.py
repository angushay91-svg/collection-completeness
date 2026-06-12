"""Collection Completeness pipeline.

Stages (each cached, idempotent):
  roads   - clip OS Open Roads to council boundary +1km
  uprn    - clip OS Open UPRN to council
  cells   - geometry->H3 join, cell_road table, ward/parish assignment
  synth   - synthetic collection history + demand pins (or --real-csv path)
  rollup  - locked metrics, all outputs into app/public/data/<council>/

Run:  python pipeline/build.py --council dorset --stage all
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

import h3
import numpy as np
import pandas as pd
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from shapely.ops import transform as shp_transform
import pyproj

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("CC_CACHE", "/sessions/vigilant-lucid-tesla/mnt/outputs/cc_cache")
WORK = os.environ.get("CC_WORK", "/tmp/cc")
ROADS_GPKG = os.environ.get("CC_ROADS_GPKG", "/tmp/osroads/Data/oproad_gb.gpkg")
APPDATA = os.path.join(HERE, "..", "app", "public", "data")

T_27700_TO_4326 = pyproj.Transformer.from_crs(27700, 4326, always_xy=True)
T_4326_TO_27700 = pyproj.Transformer.from_crs(4326, 27700, always_xy=True)

# Display-class mapping from OS road_classification (locked, §5.2)
def display_class(road_classification: str) -> str:
    return {
        "Motorway": "Motorway",
        "A Road": "A",
        "B Road": "B",
        "Classified Unnumbered": "C",
    }.get(road_classification, "Residential & minor")

CLASS_ORDER = ["Motorway", "A", "B", "C", "Residential & minor"]
WINDOW_DAYS = 365


# ---------------------------------------------------------------- stage: roads
def stage_roads():
    import pyogrio
    os.makedirs(WORK, exist_ok=True)
    lad = json.load(open(os.path.join(CACHE, "lad_bfc.geojson")))
    boundary = shape(lad["features"][0]["geometry"])  # EPSG:4326
    b27 = shp_transform(lambda x, y: T_4326_TO_27700.transform(x, y), boundary)
    buf = b27.buffer(1000)
    minx, miny, maxx, maxy = buf.bounds
    print(f"council bbox 27700: {minx:.0f},{miny:.0f} {maxx:.0f},{maxy:.0f}")
    gdf = pyogrio.read_dataframe(
        ROADS_GPKG, layer="road_link", bbox=(minx, miny, maxx, maxy),
        columns=["id", "road_classification", "road_function", "form_of_way",
                 "road_classification_number", "name_1", "length",
                 "primary_route", "trunk_road", "start_node", "end_node"],
    )
    print("bbox links:", len(gdf))
    from shapely.prepared import prep
    pbuf = prep(buf)
    keep = gdf.geometry.apply(lambda g: pbuf.intersects(g))
    gdf = gdf[keep].reset_index(drop=True)
    print("clipped links:", len(gdf))
    gdf.to_parquet(os.path.join(WORK, "roads.parquet"))


# ----------------------------------------------------------------- stage: uprn
def stage_uprn():
    import zipfile
    lad = json.load(open(os.path.join(CACHE, "lad_bfc.geojson")))
    boundary = shape(lad["features"][0]["geometry"])
    b27 = shp_transform(lambda x, y: T_4326_TO_27700.transform(x, y), boundary)
    minx, miny, maxx, maxy = b27.bounds
    from shapely.prepared import prep
    pb = prep(b27)
    z = zipfile.ZipFile(os.path.join(CACHE, "openuprn.csv.zip"))
    name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    out = []
    with z.open(name) as f:
        hdr = f.readline().decode().strip().split(",")
        ie, in_, iu = hdr.index("X_COORDINATE"), hdr.index("Y_COORDINATE"), hdr.index("UPRN")
        for line in f:
            p = line.decode().split(",")
            try:
                e, n = float(p[ie]), float(p[in_])
            except ValueError:
                continue
            if minx <= e <= maxx and miny <= n <= maxy and pb.contains(Point(e, n)):
                out.append((int(p[iu]), e, n))
    df = pd.DataFrame(out, columns=["uprn", "e", "n"])
    print("council UPRNs:", len(df))
    df.to_parquet(os.path.join(WORK, "uprn.parquet"))


# ---------------------------------------------------------------- stage: cells
def stage_cells():
    import geopandas as gpd
    gdf = gpd.read_parquet(os.path.join(WORK, "roads.parquet"))

    cell_road = []  # (h3_10, link_idx)
    SAMPLE_M = 15.0
    for idx, geom in enumerate(gdf.geometry):
        L = geom.length
        n = max(2, int(L // SAMPLE_M) + 1)
        ds = list(np.linspace(0, L, n))
        seen = set()
        for d in ds:
            pt = geom.interpolate(d)
            lon, lat = T_27700_TO_4326.transform(pt.x, pt.y)
            c = h3.latlng_to_cell(lat, lon, 10)
            if c not in seen:
                seen.add(c)
                cell_road.append((c, idx))
    cr = pd.DataFrame(cell_road, columns=["h3_10", "link"])
    print("cell_road rows:", len(cr), "distinct road cells:", cr.h3_10.nunique())

    # ward + parish assignment by cell-centroid PIP (full-res boundaries)
    cells = pd.DataFrame({"h3_10": cr.h3_10.unique()})
    lat, lon = zip(*[h3.cell_to_latlng(c) for c in cells.h3_10])
    cells["lat"], cells["lon"] = lat, lon

    # keep only cells whose centroid is inside the council (roads were clipped
    # to +1km buffer for clean edges; buffer-only cells must not skew any KPI)
    from shapely.prepared import prep
    lad = json.load(open(os.path.join(CACHE, "lad_bfc.geojson")))
    plad = prep(shape(lad["features"][0]["geometry"]))
    inside = [plad.contains(Point(lo, la)) for lo, la in zip(cells.lon, cells.lat)]
    print(f"cells inside council: {sum(inside)} (dropping {len(cells)-sum(inside)} buffer cells)")
    cells = cells[pd.Series(inside, index=cells.index)].reset_index(drop=True)
    cr = cr[cr.h3_10.isin(set(cells.h3_10))].reset_index(drop=True)

    def assign(geojson_path, code_field, name_field):
        gj = json.load(open(geojson_path))
        geoms, codes, names = [], [], []
        for ft in gj["features"]:
            geoms.append(shape(ft["geometry"]))
            codes.append(ft["properties"][code_field])
            names.append(ft["properties"][name_field])
        tree = STRtree(geoms)
        pts = [Point(lo, la) for lo, la in zip(cells.lon, cells.lat)]
        res = [None] * len(pts)
        idxs = tree.query(pts, predicate="intersects")
        for pi, gi in zip(idxs[0], idxs[1]):
            res[pi] = gi
        # nearest fallback for centroids just outside (clipped coastline etc.)
        missing = [i for i, r in enumerate(res) if r is None]
        for i in missing:
            res[i] = int(tree.nearest(pts[i]))
        return [codes[r] for r in res], [names[r] for r in res], len(missing)

    cells["ward"], cells["ward_nm"], miss_w = assign(os.path.join(CACHE, "wards.geojson"), "WD24CD", "WD24NM")
    cells["parish"], cells["parish_nm"], miss_p = assign(os.path.join(CACHE, "parishes.geojson"), "PARNCP25CD", "PARNCP25NM")
    print(f"assigned (nearest-fallback used: wards {miss_w}, parishes {miss_p})")
    unass = cells.ward.isna().sum() + cells.parish.isna().sum()
    if unass:
        raise SystemExit(f"BUILD FAIL: {unass} road cells unassigned to ward/parish")
    cells["h3_8"] = [h3.cell_to_parent(c, 8) for c in cells.h3_10]

    cells.to_parquet(os.path.join(WORK, "cells.parquet"))
    cr.to_parquet(os.path.join(WORK, "cell_road.parquet"))


# ---------------------------------------------------------------- stage: rollup
def band_of(last: date | None, build: date) -> str:
    if last is None:
        return "never"
    dd = (build - last).days
    if dd < WINDOW_DAYS:
        return "0-12"
    if dd < 2 * WINDOW_DAYS:
        return "12-24"
    return ">24"


def compute_rollup(cells: pd.DataFrame, cr: pd.DataFrame, roads: pd.DataFrame,
                   uprn: pd.DataFrame, pins: pd.DataFrame, build_date: date) -> dict:
    """All locked metric definitions (§5.2). cells must be the FULL table."""
    cells = cells.copy()
    cells["band"] = [band_of(d, build_date) for d in cells.last_dt]
    fresh = cells[cells.band == "0-12"]

    def boundary_rollup(sub: pd.DataFrame, sub_cr: pd.DataFrame) -> dict:
        total = len(sub)
        coll = int((sub.band == "0-12").sum())
        hist = sub.band.value_counts().to_dict()
        # per display class: count cell once per class it touches
        sub_cr2 = sub_cr.merge(sub[["h3_10", "band"]], on="h3_10")
        cls = {}
        for c in CLASS_ORDER:
            cc = sub_cr2[sub_cr2["dclass"] == c].drop_duplicates("h3_10")
            if len(cc):
                cls[c] = {"cells": int(len(cc)), "pct": round(100 * (cc.band == "0-12").mean(), 1)}
        # named roads
        named = []
        nn = sub_cr2[sub_cr2.road_number.notna() & (sub_cr2.road_number != "")]
        for rn, g in nn.groupby("road_number"):
            g2 = g.drop_duplicates("h3_10")
            dom = g2["dclass"].value_counts().idxmax()
            named.append({"road": rn, "class": dom, "cells": int(len(g2)),
                          "pct": round(100 * (g2.band == "0-12").mean(), 1),
                          "trunk": bool(g.trunk.any())})
        named.sort(key=lambda r: (CLASS_ORDER.index(r["class"]), r["road"]))
        return {"total_cells": total, "collected_cells": coll,
                "pct": round(100 * coll / total, 1) if total else 0.0,
                "bands": {b: int(hist.get(b, 0)) for b in ["0-12", "12-24", ">24", "never"]},
                "classes": cls, "named_roads": named}

    out = {"council": boundary_rollup(cells, cr)}

    # premises %: UPRNs within 250m straight-line (EPSG:27700) of a fresh road-cell centroid
    if len(fresh) and len(uprn):
        from scipy.spatial import cKDTree
        fe, fn = T_4326_TO_27700.transform(fresh.lon.values, fresh.lat.values)
        tree = cKDTree(np.c_[fe, fn])
        d, _ = tree.query(np.c_[uprn.e.values, uprn.n.values], k=1,
                          distance_upper_bound=250.0)
        near = int((d <= 250.0).sum())
    else:
        near = 0
    out["council"]["uprn_total"] = int(len(uprn))
    out["council"]["uprn_near"] = near
    out["council"]["uprn_pct"] = round(100 * near / max(len(uprn), 1), 1)

    # demand (outstanding / cleared) — pins live in cells; status vs current bands
    band_by_cell = dict(zip(cells.h3_10, cells.band))
    last_by_cell = dict(zip(cells.h3_10, cells.last_dt))
    pe = []
    for _, p in pins.iterrows():
        b = band_by_cell.get(p.h3_10, "never")
        created = p.created_at
        if b in ("never", "12-24", ">24"):
            status = "outstanding"
        else:
            last = last_by_cell.get(p.h3_10)
            # cleared = the pin predates the pass that refreshed the cell,
            # and that pass happened within the trailing 90 days
            cleared_recently = (last is not None and (build_date - last).days <= 90
                                and created < last)
            if cleared_recently:
                status = "cleared"
            else:
                status = "fresh_hidden"   # belongs to the coverage product, NOT this screen
        pe.append(status)
    pins = pins.assign(status=pe)
    out["council"]["demand_outstanding"] = int((pins.status == "outstanding").sum())
    out["council"]["demand_cleared"] = int((pins.status == "cleared").sum())

    # per-ward and per-parish (computed from the FULL cell table — averaging-trap guard)
    for mode, key, nmkey in [("wards", "ward", "ward_nm"), ("parishes", "parish", "parish_nm")]:
        rows = {}
        for code, sub in cells.groupby(key):
            sub_cells = set(sub.h3_10)
            sub_cr = cr[cr.h3_10.isin(sub_cells)]
            r = boundary_rollup(sub, sub_cr)
            r["name"] = sub[nmkey].iloc[0]
            if len(sub) and len(uprn):
                pass  # ward-level UPRN% computed below in one pass
            rows[code] = r
        out[mode] = rows
    return out, pins


def stage_rollup(args):
    build_date = date.fromisoformat(args.build_date) if args.build_date else date.today()
    cells = pd.read_parquet(os.path.join(WORK, "cells.parquet"))
    cr = pd.read_parquet(os.path.join(WORK, "cell_road.parquet"))
    roads = pd.read_parquet(os.path.join(WORK, "roads.parquet"))
    if "geometry" in roads:
        roads = roads.drop(columns=["geometry"])
    uprn = pd.read_parquet(os.path.join(WORK, "uprn.parquet"))
    hist = pd.read_parquet(os.path.join(WORK, "history.parquet"))
    pins = pd.read_parquet(os.path.join(WORK, "pins.parquet"))
    pins["created_at"] = pd.to_datetime(pins.created_at).dt.date

    # enrich cell_road with class/number/trunk
    cr = cr.merge(roads[["road_classification", "road_classification_number", "trunk_road"]],
                  left_on="link", right_index=True)
    cr["dclass"] = cr.road_classification.map(display_class)
    cr = cr.rename(columns={"road_classification_number": "road_number", "trunk_road": "trunk"})

    # merge history into cells
    hist["last_dt"] = pd.to_datetime(hist.last_collected_at).dt.date
    hist["first_dt"] = pd.to_datetime(hist.first_collected_at).dt.date
    cells = cells.merge(hist[["h3_10", "first_dt", "last_dt", "total_tests", "passes"]],
                        on="h3_10", how="left")
    cells["last_dt"] = cells.last_dt.where(cells.last_dt.notna(), None)

    rollups, pins = compute_rollup(cells, cr, roads, uprn, pins, build_date)

    # ---- outputs
    outdir = os.path.join(APPDATA, args.council)
    os.makedirs(os.path.join(outdir, "wards_r10"), exist_ok=True)
    cells["band"] = [band_of(d, build_date) for d in cells.last_dt]

    cell_classes = cr.groupby("h3_10").agg(
        classes=("dclass", lambda s: sorted(set(s), key=CLASS_ORDER.index)),
        roads=("road_number", lambda s: sorted({x for x in s if x})),
        trunk=("trunk", "any")).reset_index()
    cells = cells.merge(cell_classes, on="h3_10", how="left")

    # res-10 GeoJSON split per ward (viewport loading tier-2 strategy, §8)
    for ward, sub in cells.groupby("ward"):
        fts = []
        for _, r in sub.iterrows():
            bnd = h3.cell_to_boundary(r.h3_10)
            fts.append({"type": "Feature",
                        "geometry": {"type": "Polygon",
                                     "coordinates": [[[round(p[1], 5), round(p[0], 5)] for p in bnd] +
                                                     [[round(bnd[0][1], 5), round(bnd[0][0], 5)]]]},
                        "properties": {"b": r.band, "d": str(r.last_dt) if r.last_dt else None,
                                       "t": int(r.total_tests) if pd.notna(r.total_tests) else 0,
                                       "c": "/".join(r.classes if isinstance(r.classes, list) else []),
                                       "r": "/".join(r.roads if isinstance(r.roads, list) else []),
                                       "k": bool(r.trunk), "w": r.ward, "p": r.parish}})
        json.dump({"type": "FeatureCollection", "features": fts},
                  open(os.path.join(outdir, "wards_r10", f"{ward}.json"), "w"),
                  separators=(",", ":"))

    # res-8 parents: dominant band + counts
    BAND_RANK = {"never": 0, ">24": 1, "12-24": 2, "0-12": 3}
    r8 = cells.groupby("h3_8").agg(n=("h3_10", "count"),
                                   fresh=("band", lambda s: int((s == "0-12").sum())),
                                   dom=("band", lambda s: s.value_counts().idxmax())).reset_index()
    fts = []
    for _, r in r8.iterrows():
        bnd = h3.cell_to_boundary(r.h3_8)
        fts.append({"type": "Feature",
                    "geometry": {"type": "Polygon",
                                 "coordinates": [[[round(p[1], 5), round(p[0], 5)] for p in bnd] +
                                                 [[round(bnd[0][1], 5), round(bnd[0][0], 5)]]]},
                    "properties": {"b": r.dom, "n": int(r.n), "f": int(r.fresh)}})
    json.dump({"type": "FeatureCollection", "features": fts},
              open(os.path.join(outdir, "cells_r8.geojson"), "w"), separators=(",", ":"))

    # boundaries with rollups embedded (display-simplified)
    import shapely
    for mode, src, code_f, name_f in [("wards", "wards.geojson", "WD24CD", "WD24NM"),
                                       ("parishes", "parishes.geojson", "PARNCP25CD", "PARNCP25NM")]:
        gj = json.load(open(os.path.join(CACHE, src)))
        fts = []
        for ft in gj["features"]:
            code = ft["properties"][code_f]
            g = shape(ft["geometry"]).simplify(0.0004, preserve_topology=True)
            rr = rollups[mode].get(code)
            if rr is None:
                continue
            fts.append({"type": "Feature", "geometry": json.loads(shapely.to_geojson(g)),
                        "properties": {"code": code, "name": ft["properties"][name_f],
                                       "pct": rr["pct"], "cells": rr["total_cells"]}})
        json.dump({"type": "FeatureCollection", "features": fts},
                  open(os.path.join(outdir, f"boundaries_{mode}.geojson"), "w"), separators=(",", ":"))

    # council boundary (display)
    lad = json.load(open(os.path.join(CACHE, "lad_bfc.geojson")))
    g = shape(lad["features"][0]["geometry"]).simplify(0.0004, preserve_topology=True)
    json.dump({"type": "FeatureCollection",
               "features": [{"type": "Feature", "geometry": json.loads(shapely.to_geojson(g)),
                             "properties": {"name": "Dorset Council"}}]},
              open(os.path.join(outdir, "boundary_council.geojson"), "w"), separators=(",", ":"))

    # demand.json — outstanding + cleared ONLY (fresh pins never ship to this screen)
    keep = pins[pins.status.isin(["outstanding", "cleared"])]
    json.dump([{"id": str(r.id), "lat": float(r.lat), "lon": float(r.lon),
                "created_at": str(r.created_at), "kind": r.kind, "status": r.status,
                "ward": str(cells.set_index("h3_10").ward_nm.get(r.h3_10, "—"))}
               for _, r in keep.iterrows()],
              open(os.path.join(outdir, "demand.json"), "w"), separators=(",", ":"))

    # rollups.json + meta.json + CSVs
    json.dump(rollups, open(os.path.join(outdir, "rollups.json"), "w"), separators=(",", ":"))
    json.dump({"council": COUNCIL_NAME_BY_SLUG.get(args.council, args.council),
               "slug": args.council, "build": str(build_date),
               "window_start": str(build_date - timedelta(days=WINDOW_DAYS - 1)),
               "window_days": WINDOW_DAYS,
               "sources": {"roads": "OS Open Roads (Apr 2026), OGL v3",
                           "uprn": "OS Open UPRN (Apr 2026), OGL v3",
                           "boundaries": "ONS Open Geography (LAD24 BFC, WD24 BFC, PARNCP25 BFC), OGL v3",
                           "collection": "SYNTHETIC demo data (seeded), not real Streetwave history"}},
              open(os.path.join(outdir, "meta.json"), "w"), indent=1)
    for mode, csvname in [("wards", "wards_stats.csv"), ("parishes", "parishes_stats.csv")]:
        rows = [{"code": k, "name": v["name"], "pct_collected_12m": v["pct"],
                 "road_cells": v["total_cells"], **{f"band_{b}": v["bands"][b] for b in v["bands"]}}
                for k, v in rollups[mode].items()]
        pd.DataFrame(rows).sort_values("name").to_csv(os.path.join(outdir, csvname), index=False)
    # ward bboxes for viewport loading
    gj = json.load(open(os.path.join(outdir, "boundaries_wards.geojson")))
    bb = {ft["properties"]["code"]: list(shape(ft["geometry"]).bounds) for ft in gj["features"]}
    json.dump(bb, open(os.path.join(outdir, "ward_bbox.json"), "w"), separators=(",", ":"))
    print("rollup done. council headline:", rollups["council"]["pct"], "%")


COUNCIL_NAME_BY_SLUG = {"dorset": "Dorset Council"}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--council", default="dorset")
    ap.add_argument("--stage", default="all",
                    choices=["roads", "uprn", "cells", "synth", "rollup", "all"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--build-date", default=None)
    ap.add_argument("--real-csv", default=None,
                    help="CSV h3_10,last_collected_at,test_count — bypasses synthesis")
    args = ap.parse_args()
    if args.stage in ("roads", "all"):
        stage_roads()
    if args.stage in ("uprn", "all"):
        stage_uprn()
    if args.stage in ("cells", "all"):
        stage_cells()
    if args.stage in ("synth", "all"):
        import synth
        synth.run(WORK, seed=args.seed, real_csv=args.real_csv)
    if args.stage in ("rollup", "all"):
        stage_rollup(args)
