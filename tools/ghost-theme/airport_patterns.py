"""Presentation adapter for the shared, pinned runway classifier."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from wtf_adsb_publisher import airport_context  # noqa: E402

runway_data = airport_context.runway_data
DATASET_VERSION = airport_context.DATASET_VERSION
DATASET_HASH = airport_context.DATASET_HASH
CLASSIFIER = airport_context.CLASSIFIER


def classify(parts, event, *, analysis_window):
    return airport_context.classify(parts, event, analysis_window=analysis_window, runways=runway_data())
