"""Small reference files for tests, written to a temporary reference directory.

The aircraft-type, identity, and notable-variant reference files do not ship:
an operator supplies them in WTF_ADSB_REFERENCE_DIR. These fixtures have the
same shape and a few hand-picked entries, enough for the tests that need a
type class, an address allocation, a nationality mark, or a curated variant.
They are not complete reference data. Each file gets its own SHA-256 pin,
patched into reference.FILES while installed.

A test module that needs them calls install() from setUpModule; the fixture
removes itself when the module's tests finish.
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wtf_adsb_publisher import reference

AIRCRAFT_TYPES = {
    "dataset_version": "reference-aircraft-types-v1",
    "types": {
        "A002": ["G1P", "L"],
        "A1": ["L1P", "M"],
        "A10": ["L2J", "M"],
        "A129": ["H2T", "L"],
        "A20": ["L2P", "M"],
        "A25": ["A1P", "L"],
        "A342": ["L4J", "H"],
        "A400": ["L4T", "H"],
        "A748": ["L2T", "M"],
        "AKNC": ["L2T", "L"],
        "AS14": ["L1P", "L"],
        "AS50": ["H1T", "L"],
        "B06": ["H1T", "L"],
        "B17": ["L4P", "M"],
        "C08T": ["L0T", "L"],
        "C172": ["L1P", "L"],
        "CE15": ["S1P", "L"],
        "CRJX": ["L2J", "M"],
        "DC10": ["L3J", "H"],
        "EFAN": ["L2E", "L"],
        "GLF6": ["L2J", "M"],
        "GLID": ["L0-", "-"],
        "H53S": ["H3T", "M"],
        "JU52": ["L3P", "M"],
        "R44": ["H1P", "L"],
        "R66": ["H1T", "L"],
        "ROAR": ["L1R", "L"],
        "SLK5": ["L1P", "L"],
        "SOL1": ["L4E", "L"],
        "TWR": ["T0-", "-"],
    },
    "earliest_year": {
        "B407": 1991,
        "B77W": 2000,
        "C172": 1951,
        "C182": 1951,
        "C208": 1980,
        "C25C": 1989,
        "DC3": 1932,
        "GLF6": 2005,
        "P51": 1937,
    },
    "group_designators": {
        "BALL": "balloon",
        "DRON": "drone",
        "GLID": "glider",
        "GYRO": "gyroplane",
        "PARA": "parachute or paraglider",
        "SHIP": "airship",
        "TWR": "fixed obstacle",
        "UHEL": "ultralight helicopter",
        "ULAC": "ultralight",
        "ZZZZ": "unassigned",
    },
    "glider_types": ["AS14", "GLID"],
    "uav_types": ["AKNC", "DRON"],
}
IDENTITY = {
    "dataset_version": "reference-identity-v1",
    "allocations": [
        ["3c0000", "3fffff", "de"],
        ["400000", "43ffff", "gb"],
        ["490000", "497fff", "pt"],
        ["718000", "71ffff", "kr"],
        ["7c0000", "7fffff", "au"],
        ["896000", "896fff", "ae"],
        ["a00000", "afffff", "us"],
    ],
    "military_allocations": [["adf7c8", "afffff"], ["497c00", "497cff"]],
    "countries": {
        "ae": "United Arab Emirates",
        "au": "Australia",
        "ca": "Canada",
        "co": "Colombia",
        "de": "Germany",
        "do": "Dominican Republic",
        "fr": "France",
        "gb": "United Kingdom",
        "jp": "Japan",
        "kr": "South Korea",
        "pa": "Panama",
        "pt": "Portugal",
        "us": "United States",
        "ve": "Venezuela",
    },
    "country_groups": {"gb": ["gb", "bm", "ky", "gi"]},
    "registration_prefixes": {
        "A6": ["ae"],
        "D": ["de"],
        "F": ["fr"],
        "G": ["gb"],
        "HI": ["do"],
        "HK": ["co"],
        "HL": ["kr"],
        "HP": ["pa"],
        "JA": ["jp"],
        "N": ["us"],
        "VH": ["au"],
        "YV": ["ve"],
    },
    "unhyphenated_prefixes": ["N", "JA", "HL", "HI", "HP", "HK", "YV"],
    "operator_designators": "AAL DAL UAL",
}
NOTABLE_VARIANTS = {
    "dataset_version": "reference-notable-variants-v1",
    "variants": [{"descriptions": ["Boeing E-6B Mercury"], "family": "E-6", "label": "E-6B Mercury"}],
    "mission_callsign_prefixes": ["AF1", "SAM"],
}
DOCUMENTS = {
    "aircraft-types": AIRCRAFT_TYPES,
    "identity": IDENTITY,
    "notable-variants": NOTABLE_VARIANTS,
}


def document(content):
    """A pinned reference document: schema version 1 and its canonical content hash."""
    content = {"schema_version": 1, **content}
    return {**content, "content_sha256": reference.canonical_hash(content)}


def write(directory, documents=None):
    """Write each document as its reference file; return {name: (filename, file sha256)} pins."""
    pins = {}
    for name, content in (DOCUMENTS if documents is None else documents).items():
        filename = reference.FILES[name][0]
        raw = json.dumps(document(content), sort_keys=True).encode()
        Path(directory, filename).write_bytes(raw)
        pins[name] = (filename, hashlib.sha256(raw).hexdigest())
    return pins


def install():
    """Point the reference module at fixture files until the calling module's tests end."""
    folder = tempfile.TemporaryDirectory()
    patches = (
        mock.patch.dict(reference.FILES, write(folder.name)),
        mock.patch.dict(os.environ, {reference.ENVIRONMENT: folder.name}),
    )
    for patch in patches:
        patch.start()
    reference.clear_cache()

    def remove():
        for patch in reversed(patches):
            patch.stop()
        folder.cleanup()
        reference.clear_cache()

    unittest.addModuleCleanup(remove)
    return Path(folder.name)
