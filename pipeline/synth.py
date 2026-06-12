"""Synthetic collection history: bin lorries on the road graph (seeded, reproducible).

Also supports the real-data path: a CSV with columns h3_10,last_collected_at,test_count
bypasses synthesis entirely (everything downstream is source-agnostic).
"""
from __future__ import annotations
import json
import os
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

MONTHS = 30          # simulated history length
WEEKS = MONTHS * 52 // 12
DROPOUT = 0.15       # per route-week
PRIVATE_FRAC = 0.04  # residential dead-ends excluded entirely
A35_KEEP = 0.70      # A35 deliberately only 70% routed
STALE_AFTER_WEEK = 18 * 52 // 12  # one ward loses all routes after month 18


def run(work: str, seed: int = 42, real_csv: str | None = None, fleet_scale: float = 2.0):
    # fleet_scale: calibration multiplier on routes-per-depot (spec base 8-14).
    # At 1.0 the spec fleet can only sweep ~27% of Dorset's 7,100km network;
    # 2.0 lands the synthetic headline near observed real-world completeness.
    cells = pd.read_parquet(os.path.join(work, "cells.parquet"))
    cr = pd.read_parquet(os.path.join(work, "cell_road.parquet"))
    roads = pd.read_parquet(os.path.join(work, "roads.parquet"))
    uprn = pd.read_parquet(os.path.join(work, "uprn.parquet"))
    rng = np.random.default_rng(seed)
    today = date.today()

    if real_csv:
        df = pd.read_csv(real_csv)
        hist = pd.DataFrame({
            "h3_10": df.h3_10,
            "first_collected_at": df.last_collected_at,
            "last_collected_at": df.last_collected_at,
            "total_tests": df.test_count, "passes": 1})
        hist.to_parquet(os.path.join(work, "history.parquet"))
        _pins(cells, hist, rng, today, work)
        return

    # ---- road graph: nodes = endpoints snapped to 1m
    import shapely
    geoms = shapely.from_wkb(roads.geometry.values) if roads.geometry.dtype == object else roads.geometry.values
    starts, ends = [], []
    for g in geoms:
        c = shapely.get_coordinates(g)
        starts.append((round(c[0][0]), round(c[0][1])))
        ends.append((round(c[-1][0]), round(c[-1][1])))
    n_links = len(roads)
    adj = defaultdict(list)  # node -> [(link_idx, other_node)]
    for i, (s, e) in enumerate(zip(starts, ends)):
        adj[s].append((i, e))
        adj[e].append((i, s))

    # link attractiveness: UPRN density near link midpoint (1km grid)
    mid = np.array([[(s[0] + e[0]) / 2, (s[1] + e[1]) / 2] for s, e in zip(starts, ends)])
    grid = defaultdict(int)
    for e_, n_ in zip(uprn.e.values, uprn.n.values):
        grid[(int(e_ // 1000), int(n_ // 1000))] += 1
    w_link = np.array([1.0 + grid[(int(m[0] // 1000), int(m[1] // 1000))] for m in mid])

    # exclusions
    cls = roads.road_classification.values
    rcn = roads.road_classification_number.fillna("").values
    func = roads.road_function.fillna("").values
    motorway = cls == "Motorway"
    deg = defaultdict(int)
    for s, e in zip(starts, ends):
        deg[s] += 1
        deg[e] += 1
    deadend = np.array([deg[s] == 1 or deg[e] == 1 for s, e in zip(starts, ends)])
    residential = np.array([("Residential" in f) or ("Local" in f) for f in func])
    private_pool = np.where(deadend & residential)[0]
    private = set(rng.choice(private_pool, size=int(PRIVATE_FRAC * len(private_pool)), replace=False))
    a35 = set(np.where(rcn == "A35")[0])
    a35_dropped = set(rng.choice(list(a35), size=int((1 - A35_KEEP) * len(a35)), replace=False)) if a35 else set()
    banned = private | set(np.where(motorway)[0]) | a35_dropped
    print(f"exclusions: private dead-ends {len(private)}, motorway {motorway.sum()}, A35 dropped {len(a35_dropped)}/{len(a35)}")

    # depots: 4 densest UPRN 1km cells
    top = sorted(grid.items(), key=lambda kv: -kv[1])
    depots = []
    for (gx, gy), _ in top:
        c = (gx * 1000 + 500, gy * 1000 + 500)
        if all(abs(c[0] - d[0]) + abs(c[1] - d[1]) > 8000 for d in depots):
            depots.append(c)
        if len(depots) == 4:
            break
    nodes = list(adj.keys())
    node_arr = np.array(nodes)
    from scipy.spatial import cKDTree
    ntree = cKDTree(node_arr)
    depot_nodes = [nodes[ntree.query(d)[1]] for d in depots]

    # ---- routes: contiguous street sweeps, 25–60 km each
    # A bin round serves a contiguous neighbourhood: per depot, expand outward
    # (best-first by UPRN density) over unvisited links, chunking into routes.
    import heapq
    lengths = roads.length.values
    routes = []
    for dn in depot_nodes:
        n_routes = int(rng.integers(8, 15) * fleet_scale)
        targets = rng.uniform(25000, 60000, size=n_routes)
        seen: set[int] = set()
        heap = []          # (-uprn_weight + jitter, node)
        heapq.heappush(heap, (0.0, dn))
        in_heap = {dn}
        cur, walked, ri = [], 0.0, 0
        while heap and ri < n_routes:
            _, node = heapq.heappop(heap)
            for li, other in adj[node]:
                if li in banned or li in seen:
                    continue
                seen.add(li)
                cur.append(li)
                walked += lengths[li]
                if other not in in_heap:
                    in_heap.add(other)
                    heapq.heappush(heap, (-w_link[li] * float(rng.uniform(0.5, 1.5)), other))
                if walked >= targets[ri]:
                    routes.append(cur)
                    cur, walked = [], 0.0
                    ri += 1
                    if ri >= n_routes:
                        break
        if cur:
            routes.append(cur)
    distinct = len({li for r in routes for li in r})
    print("routes:", len(routes), "avg links/route:", int(np.mean([len(r) for r in routes])),
          "distinct links covered:", distinct, f"({100*distinct/len(roads):.0f}% of network)")

    # stale ward: the ward whose routes stop after month 18 (visible stale patch)
    link_cells = cr.groupby("link").h3_10.apply(list)
    cell_ward = dict(zip(cells.h3_10, cells.ward))
    # stale ward must be one the fleet actually covers, so it visibly ages out:
    # take the ward with the most routed cells outside the four depot home wards.
    routed_cells = {c for links in routes for li in links for c in link_cells.get(li, [])}
    cov = cells[cells.h3_10.isin(routed_cells)].ward.value_counts()
    stale_ward = cov.index[4] if len(cov) > 4 else cov.index[-1]
    print("stale ward:", stale_ward)

    # ---- weekly history (vectorised: per-route active weeks, then merge per cell)
    start_day = today - timedelta(weeks=WEEKS)
    # 25% of routes are retired partway through the history (service changes),
    # which is what populates the 12-24m and >24m age bands realistically.
    route_weeks = []
    for _ in routes:
        wks = np.where(rng.random(WEEKS) >= DROPOUT)[0]
        if rng.random() < 0.25:
            wks = wks[wks <= rng.integers(13, WEEKS - 8)]
        route_weeks.append(wks)
    route_cells = [{c for li in links for c in link_cells.get(li, [])} for links in routes]
    first_w, last_w, passes = {}, {}, defaultdict(int)
    for weeks, cset in zip(route_weeks, route_cells):
        if len(weeks) == 0:
            continue
        wmin, wmax, n = int(weeks.min()), int(weeks.max()), len(weeks)
        for c in cset:
            if cell_ward.get(c) == stale_ward:
                w2 = weeks[weeks <= STALE_AFTER_WEEK]
                if len(w2) == 0:
                    continue
                a, b, k = int(w2.min()), int(w2.max()), len(w2)
            else:
                a, b, k = wmin, wmax, n
            first_w[c] = min(first_w.get(c, a), a)
            last_w[c] = max(last_w.get(c, b), b)
            passes[c] += k
    def wdate(w):
        return start_day + timedelta(weeks=int(w), days=3)
    keys = list(first_w.keys())
    hist = pd.DataFrame({"h3_10": keys,
                         "first_collected_at": [str(wdate(first_w[c])) for c in keys],
                         "last_collected_at": [str(wdate(last_w[c])) for c in keys],
                         "total_tests": [int(rng.integers(2, 9, size=passes[c]).sum()) for c in keys],
                         "passes": [passes[c] for c in keys]})
    print(f"collected cells: {len(hist)} of {len(cells)} road cells")
    hist.to_parquet(os.path.join(work, "history.parquet"))
    _pins(cells, hist, rng, today, work)


def _pins(cells: pd.DataFrame, hist: pd.DataFrame, rng, today: date, work: str):
    """30 demand pins: 60% outstanding (never/>12m), 25% fresh (hidden), 15% recently cleared."""
    last = dict(zip(hist.h3_10, pd.to_datetime(hist.last_collected_at).dt.date))
    never = [c for c in cells.h3_10 if c not in last]
    stale = [c for c, d in last.items() if (today - d).days >= 365]
    fresh = [c for c, d in last.items() if (today - d).days < 365]
    just_refreshed = [c for c, d in last.items() if (today - d).days <= 90]
    rows = []
    def add(n, pool, kind_age):
        for _ in range(n):
            c = pool[int(rng.integers(0, len(pool)))]
            lat, lon = __import__("h3").cell_to_latlng(c)
            rows.append({"id": f"pin{len(rows)+1:03d}", "lat": lat, "lon": lon, "h3_10": c,
                         "created_at": str(today - timedelta(days=int(rng.integers(*kind_age)))),
                         "kind": ["map_the_gap", "checker_request"][int(rng.integers(0, 2))]})
    add(18, (never + stale) or list(cells.h3_10), (10, 300))       # 60% outstanding
    add(7, fresh or list(cells.h3_10), (10, 300))                  # 25% fresh — must be filtered out
    add(5, just_refreshed or fresh or list(cells.h3_10), (95, 300))  # 15% cleared this period
    pd.DataFrame(rows).to_parquet(os.path.join(work, "pins.parquet"))
    print("pins:", len(rows))
