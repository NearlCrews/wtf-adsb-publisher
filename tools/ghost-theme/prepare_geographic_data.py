#!/usr/bin/env python3
"""Create the checked-in regional map bundle from pinned public source downloads.

This maintenance command reads local originals; site builds never download maps.
Source URLs and exact raw hashes are recorded in the resulting data manifest.
"""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from geographic_context import clip_lines, clip_ring, inside, intersects, ring_area

from wtf_adsb_publisher.places import content_hash

COVERAGE = (-93, 38, -74, 50)
SOURCES = (
    ("ne_10m_lakes.geojson", "2d036f53dedec578001c5c30c2959ee7d4eebc1306900fa4367c49929ec8f2d9"),
    (
        "ne_10m_rivers_lake_centerlines.geojson",
        "bb854a900ecbd3b408df46d5e16e3e0f974ba55993f9d8b5c26e855273c0905a",
    ),
    (
        "ne_10m_admin_1_states_provinces_lines.geojson",
        "1a1f30ccaaf4cc9c4bde34266f0b8cbb955d3a4cf254b756912255f2ec7c75b6",
    ),
    (
        "ne_10m_populated_places_simple.geojson",
        "fd3fa867a320cbd5c5b6bb5bc550afeec2939fb2cef688e508007282a55ac42f",
    ),
    ("airports.csv", "ee5a1ebce3661288cec7297f6bb21112b7448e7dc41f62cda1f2315989a57d21"),
    ("2025_gaz_place_26.txt", "48a2aff5570a837b3fd2b2c6012f7995a090504d89985619073a969201e53dd3"),
)
REVISIONS = {
    "natural-earth-vector": "ca96624a56bd078437bca8184e78163e5039ad19",
    "ourairports-data": "07f86e80d3f15296c3ff971f49c33ddb331c8d8a",
}


def extent(points):
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def rounded(points):
    result = []
    for point in points:
        value = [round(point[0], 5), round(point[1], 5)]
        if not result or result[-1] != value:
            result.append(value)
    return result


def source_record(filename, expected):
    if filename == "2025_gaz_place_26.txt":
        revision = "2025"
        url = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/" + filename
        license_url = "https://www2.census.gov/foia/ds_policies/ds027.pdf"
    elif filename == "airports.csv":
        revision = REVISIONS["ourairports-data"]
        url = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/" + revision + "/" + filename
        license_url = "https://ourairports.com/data/"
    else:
        revision = REVISIONS["natural-earth-vector"]
        url = (
            "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
            + revision
            + "/geojson/"
            + filename
        )
        license_url = "https://www.naturalearthdata.com/about/terms-of-use/"
    return {
        "name": filename,
        "url": url,
        "revision": revision,
        "sha256": expected,
        "license": "Public domain",
        "license_url": license_url,
    }


def clean_name(value):
    """A display name with single spaces (Natural Earth writes "Washington,  D.C.")."""
    return " ".join(str(value or "").split())


def add_michigan_places(data, raw):
    """Use Census reference points without duplicating existing Michigan cities."""
    existing = {p["name"]: p for p in data["places"] if p["kind"] == "city" and p.get("region") == "Michigan"}
    replacements = []
    for row in csv.DictReader(raw.decode().splitlines(), delimiter="|"):
        if row["USPS"] != "MI" or row["LSAD"] not in {"25", "47", "57"}:
            raise ValueError("Unexpected Michigan Gazetteer place")
        name = clean_name(re.sub(r" (?:city|village|CDP)$", "", row["NAME"]))
        point = [float(row["INTPTLONG"]), float(row["INTPTLAT"])]
        if not inside(point, COVERAGE):
            continue
        replacements.append(
            {
                "kind": "city",
                "name": name,
                "region": "Michigan",
                "point": rounded([point])[0],
                "rank": existing.get(name, {}).get("rank", 10),
                "census_geoid": row["GEOID"],
                "source_name": row["NAME"],
            }
        )
    replaced = {p["name"] for p in replacements}
    data["places"] = [
        p
        for p in data["places"]
        if not (p["kind"] == "city" and p.get("region") == "Michigan" and p["name"] in replaced)
    ] + replacements


def prepare(folder):
    data = {
        "schema_version": 1,
        "dataset_version": "great-lakes-v1",
        "coverage": list(COVERAGE),
        "coordinate_system": "Source longitude, latitude in decimal degrees; no datum transformation",
        "sources": [],
        "lakes": [],
        "rivers": [],
        "boundaries": [],
        "places": [],
    }
    for filename, expected in SOURCES:
        raw = (folder / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Source hash mismatch: " + filename)
        data["sources"].append(source_record(filename, expected))
        if filename == "2025_gaz_place_26.txt":
            add_michigan_places(data, raw)
            continue
        if filename == "airports.csv":
            for airport in csv.DictReader(raw.decode().splitlines()):
                if airport["type"] not in {"large_airport", "medium_airport", "small_airport"}:
                    continue
                if airport["type"] == "small_airport" and not re.fullmatch(r"[A-Z]{4}", airport["ident"]):
                    continue
                point = [float(airport["longitude_deg"]), float(airport["latitude_deg"])]
                if not inside(point, COVERAGE):
                    continue
                code = airport["iata_code"] or airport["local_code"] or airport["ident"]
                data["places"].append(
                    {
                        "kind": "airport",
                        "name": clean_name(airport["name"]),
                        "code": code,
                        "point": rounded([point])[0],
                        "rank": {"large_airport": 0, "medium_airport": 1, "small_airport": 2}[
                            airport["type"]
                        ],
                    }
                )
            continue
        for feature in json.loads(raw)["features"]:
            geometry, properties = feature["geometry"], feature["properties"]
            if not geometry:
                continue
            coordinates, kind = geometry["coordinates"], geometry["type"]
            if properties.get("featurecla") == "Lake Centerline":
                continue
            name = clean_name(properties.get("name_en") or properties.get("name"))
            if kind == "Point":
                if inside(coordinates, COVERAGE):
                    data["places"].append(
                        {
                            "kind": "city",
                            "name": name,
                            "region": properties.get("adm1name", ""),
                            "point": rounded([coordinates])[0],
                            "rank": properties.get("scalerank", 10),
                        }
                    )
            elif kind in {"Polygon", "MultiPolygon"}:
                for polygon in [coordinates] if kind == "Polygon" else coordinates:
                    if not intersects(extent(polygon[0]), COVERAGE):
                        continue
                    rings = []
                    for index, original in enumerate(polygon):
                        ring = rounded(clip_ring(original, COVERAGE))
                        if len(ring) < 3 or not ring_area(ring):
                            continue
                        if (ring_area(ring) > 0) != (index == 0):
                            ring.reverse()
                        ring.append(ring[0])
                        rings.append(ring)
                    if rings:
                        lake = {"name": name, "bounds": extent(rings[0]), "rings": rings}
                        original_bounds = extent(polygon[0])
                        if not (
                            inside(original_bounds[:2], COVERAGE) and inside(original_bounds[2:], COVERAGE)
                        ):
                            lake["shorelines"] = [
                                rounded(line) for ring in polygon for line in clip_lines(ring, COVERAGE)
                            ]
                        data["lakes"].append(lake)
            elif kind in {"LineString", "MultiLineString"}:
                lines = []
                for line in [coordinates] if kind == "LineString" else coordinates:
                    if intersects(extent(line), COVERAGE):
                        lines.extend(rounded(part) for part in clip_lines(line, COVERAGE))
                lines = [line for line in lines if len(line) >= 2]
                if lines:
                    layer = "boundaries" if "admin_1" in filename else "rivers"
                    data[layer].append(
                        {
                            "name": name,
                            "bounds": extent([point for line in lines for point in line]),
                            "lines": lines,
                        }
                    )
    for layer in ("lakes", "rivers", "boundaries", "places"):
        data[layer].sort(key=lambda feature: json.dumps(feature, sort_keys=True))
    data["processing"] = (
        "Regional subset clipped to coverage; coordinates rounded to five decimals. Lake ring winding preserves islands; artificial lake centerlines are omitted. Natural Earth 1:10 million generalization is retained, not enhanced. Michigan city, village and census-designated-place reference points use the 2025 Census Gazetteer internal points, not downtown coordinates or boundaries. Matching Natural Earth city labels are replaced while retaining their display rank. Airports include large and medium facilities plus small airports with four-letter identifiers; closed airports and heliports are omitted. Airport points are references, not evidence of airport use."
    )
    data["content_sha256"] = content_hash(data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "data/great-lakes-v1.json")
    options = parser.parse_args()
    data = prepare(options.input)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(
        json.dumps(
            {key: len(data[key]) for key in ("lakes", "rivers", "boundaries", "places")}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
