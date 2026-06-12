"""Pinned source datasets for Collection Completeness.

All inputs are open data. Each entry records: URL, licence, retrieval date.
Retrieved: 2026-06-12 (build night). VERIFYed against live APIs on that date.
"""
from __future__ import annotations
import hashlib
import os
import subprocess
import sys

CACHE = os.environ.get("CC_CACHE", os.path.join(os.path.dirname(__file__), "..", "cache"))

# Dorset Council (unitary). ONS LAD code:
LAD_CODE = "E06000059"
COUNCIL_SLUG = "dorset"
COUNCIL_NAME = "Dorset Council"

SOURCES = {
    # OS Open Roads — Ordnance Survey Open Data, Open Government Licence v3.
    # GB-wide GeoPackage via OS Downloads API (no key needed for OpenData products).
    # Attribute names VERIFYed 2026-06-12: road_classification, road_classification_number,
    # name_1, trunk_road, primary_route on layer 'road_link'.
    "openroads": {
        "url": "https://api.os.uk/downloads/v1/products/OpenRoads/downloads?area=GB&format=GeoPackage&redirect",
        "file": "openroads.gpkg.zip",
        "licence": "OGL v3 (OS OpenData)",
        "retrieved": "2026-06-12",
    },
    # OS Open UPRN — UPRN + easting/northing (EPSG:27700) + lat/lon. OGL v3.
    # NO AddressBase anywhere: this product carries no classification data by design.
    "openuprn": {
        "url": "https://api.os.uk/downloads/v1/products/OpenUPRN/downloads?area=GB&format=CSV&redirect",
        "file": "openuprn.csv.zip",
        "licence": "OGL v3 (OS OpenData)",
        "retrieved": "2026-06-12",
    },
    # ONS Open Geography Portal (all OGL v3), via ArcGIS REST GeoJSON queries,
    # filtered server-side to Dorset. Vintages VERIFYed 2026-06-12 by service search;
    # services used are recorded in README.
    "lad_bfc": {
        "url": (
            "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
            "Local_Authority_Districts_December_2024_Boundaries_UK_BFC/FeatureServer/0/query"
            f"?where=LAD24CD='{LAD_CODE}'&outFields=*&f=geojson"
        ),
        "file": "lad_bfc.geojson",
        "licence": "OGL v3 (ONS / OS)",
        "retrieved": "2026-06-12",
    },
    "wards": {
        "url": (
            "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
            "Wards_December_2024_Boundaries_UK_BFC/FeatureServer/0/query"
            f"?where=LAD24CD='{LAD_CODE}'&outFields=*&f=geojson"
        ),
        "file": "wards.geojson",
        "licence": "OGL v3 (ONS / OS)",
        "retrieved": "2026-06-12",
    },
    # NOTE: Dec-2024 parish service has no LAD field exposed; the Dec-2025 vintage
    # (PARNCP_DEC_2025_EW_BFC, fields PARNCP25CD/PARNCP25NM/LAD25CD) is used instead.
    # NCP ("non-civil parished area") units ship as named features and are used as-is.
    "parishes": {
        "url": (
            "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
            "PARNCP_DEC_2025_EW_BFC/FeatureServer/0/query"
            f"?where=LAD25CD='{LAD_CODE}'&outFields=PARNCP25CD,PARNCP25NM,LAD25CD&f=geojson"
        ),
        "file": "parishes.geojson",
        "licence": "OGL v3 (ONS / OS)",
        "retrieved": "2026-06-12",
    },
}


def fetch(name: str) -> str:
    """Idempotent download of a pinned source into the cache. Returns local path."""
    src = SOURCES[name]
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, src["file"])
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    print(f"downloading {name} from {src['url'][:100]}…", file=sys.stderr)
    subprocess.run(["curl", "-sSL", "-C", "-", "-o", path, src["url"]], check=True)
    return path


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    for k in SOURCES:
        p = fetch(k)
        print(k, p, os.path.getsize(p), sha256(p)[:16])
