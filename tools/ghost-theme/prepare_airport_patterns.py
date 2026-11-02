#!/usr/bin/env python3
"""Prepare the offline runway subset from locally downloaded, pinned originals."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

REVISION = "07f86e80d3f15296c3ff971f49c33ddb331c8d8a"
SOURCES = {
    "airports.csv": "ee5a1ebce3661288cec7297f6bb21112b7448e7dc41f62cda1f2315989a57d21",
    "runways.csv": "916f14a3fb44388df07bb708e514b4fffc10b38765821ee5765857664b4082df",
}


def prepare(folder):
    originals = {}
    for name, expected in SOURCES.items():
        raw = (folder / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"Source hash mismatch: {name}")
        originals[name] = list(csv.DictReader(raw.decode().splitlines()))
    airports = {
        row["ident"]: row
        for row in originals["airports.csv"]
        if row["type"] in {"small_airport", "medium_airport", "large_airport"}
    }
    runways = []
    for row in originals["runways.csv"]:
        if row["airport_ident"] not in airports or row["closed"] == "1":
            continue
        try:
            ends = [
                [round(float(row[f"{end}_longitude_deg"]), 6), round(float(row[f"{end}_latitude_deg"]), 6)]
                for end in ("le", "he")
            ]
        except ValueError:
            continue
        if not all(38 <= lat <= 50 and -93 <= lon <= -74 for lon, lat in ends):
            continue
        a, b = ends
        length = math.hypot(
            (a[0] - b[0]) * math.cos(math.radians((a[1] + b[1]) / 2)) * 60, (a[1] - b[1]) * 60
        )
        if not 0.1 <= length <= 4 or not row["le_ident"] or not row["he_ident"]:
            continue
        runways.append(
            {
                "airport_ident": row["airport_ident"],
                "airport_name": airports[row["airport_ident"]]["name"],
                "runway": row["le_ident"] + "/" + row["he_ident"],
                "ends": ends,
            }
        )
    runways.sort(key=lambda runway: (runway["airport_ident"], runway["runway"]))
    data = {
        "schema_version": 1,
        "dataset_version": "ourairports-runways-v1",
        "coverage": [-93, 38, -74, 50],
        "sources": [
            {
                "name": name,
                "url": f"https://raw.githubusercontent.com/davidmegginson/ourairports-data/{REVISION}/{name}",
                "sha256": digest,
                "revision": REVISION,
                "license": "Public domain",
                "license_url": "https://ourairports.com/data/",
            }
            for name, digest in SOURCES.items()
        ],
        "runways": runways,
    }
    content = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    data["content_sha256"] = hashlib.sha256(content).hexdigest()
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "data/ourairports-runways-v1.json"
    )
    args = parser.parse_args()
    args.output.write_text(json.dumps(prepare(args.input), ensure_ascii=True, indent=2) + "\n")
