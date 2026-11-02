#!/usr/bin/env python3
"""Accept passive SVG primitives and one normalized PNG in the map frame."""

import base64
import binascii
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "src"))
from raster_png import MAX_PNG_BYTES, validate_png
from raster_webp import validate_webp

from wtf_adsb_publisher import private_terms

NAMESPACE = "{http://www.w3.org/2000/svg}"
ELEMENTS = {
    "svg",
    "title",
    "desc",
    "g",
    "rect",
    "path",
    "circle",
    "text",
    "tspan",
    "line",
    "polyline",
    "image",
}
TAGS = {NAMESPACE + name for name in ELEMENTS}
MAX_SVG_BYTES = 1024 * 1024
ATTRIBUTES = {
    "width",
    "height",
    "viewBox",
    "role",
    "aria-labelledby",
    "aria-label",
    "id",
    "x",
    "y",
    "x1",
    "x2",
    "y1",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "d",
    "points",
    "transform",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "opacity",
    "stroke-opacity",
    "fill-opacity",
    "font-family",
    "font-size",
    "font-weight",
    "text-anchor",
    "dominant-baseline",
}


def _embedded_png(element):
    expected = {"x": "90", "y": "165", "width": "1020", "height": "440"}
    if (
        set(element.attrib) != {*expected, "href"}
        or any(element.get(key) != value for key, value in expected.items())
        or len(element)
        or (element.text or "").strip()
    ):
        raise ValueError("Embedded PNG must occupy only the map frame")
    href = element.get("href")
    kinds = {"data:image/png;base64,": validate_png, "data:image/webp;base64,": validate_webp}
    prefix = next((prefix for prefix in kinds if href.startswith(prefix)), None)
    if prefix is None:
        raise ValueError("Only an embedded PNG or WebP is supported")
    payload = href[len(prefix) :]
    if len(payload) > 4 * ((MAX_PNG_BYTES + 2) // 3):
        raise ValueError("Embedded PNG is too large")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Invalid embedded PNG encoding") from error
    if base64.b64encode(raw).decode("ascii") != payload:
        raise ValueError("Noncanonical embedded PNG encoding")
    kinds[prefix](raw)


def validate(svg):
    """Reject active content, URL references, foreign namespaces and XML entities."""
    if not isinstance(svg, str) or len(svg) > MAX_SVG_BYTES or len(svg.encode("utf8")) > MAX_SVG_BYTES:
        raise ValueError("Diagram is too large")
    if "<!" in svg or "<?" in svg:
        raise ValueError("XML declarations and entities are not diagram content")
    root = ElementTree.fromstring(svg)
    if root.tag != NAMESPACE + "svg" or any(
        root.get(key) != value
        for key, value in {"width": "1200", "height": "800", "viewBox": "0 0 1200 800"}.items()
    ):
        raise ValueError("Invalid diagram root or dimensions")
    images = 0
    private = private_terms.compiled()
    for element in root.iter():
        if element.tag not in TAGS:
            raise ValueError("Unsupported diagram element")
        if any(
            private.search(value)
            for value in (element.text or "", element.tail or "", *element.attrib.values())
        ):
            raise ValueError("Diagram contains a private name or receiver field")
        if element.tag == NAMESPACE + "image":
            images += 1
            if images > 1 or element not in root or root.get("transform") is not None:
                raise ValueError("A diagram may have one untransformed map image")
            _embedded_png(element)
            continue
        for name, value in element.attrib.items():
            if name not in ATTRIBUTES or re.search(r"url\s*\(|(?:https?|data|javascript):", value, re.I):
                raise ValueError("Unsupported diagram attribute")
            if name in {"fill", "stroke"} and not re.fullmatch(r"#[0-9a-fA-F]{3,8}|none", value):
                raise ValueError("Diagram paint must be a literal color")


def main():
    for record in json.load(sys.stdin):
        validate(record["svg"])


if __name__ == "__main__":
    main()
