#!/usr/bin/env python3
"""Explicitly fetch and cache a reviewed, pinned Plane Alert CSV and its license.

No schedule or runtime fetch is installed. Source files and derived metadata are
kept in a private directory; nothing is added to Git or the running receiver.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from wtf_adsb_publisher.watchlists import (
    MAX_CACHE_BYTES,
    MAX_SOURCE_BYTES,
    WatchlistError,
    parse_catalog,
    source_commit,
)


def private_report_directory(directory):
    """Create or validate an owned, mode-0700 directory before writing private files."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Use a real private directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Report directory must be owned by the current user with mode 0700")
    return directory


def write_private_report(directory, value, filename="report.json"):
    """Write JSON atomically with mode 0600 into an owned, mode-0700 directory."""
    if Path(filename).name != filename:
        raise ValueError("Use a plain report filename")
    directory = private_report_directory(directory)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=directory, prefix=".report-", delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            json.dump(value, output, indent=2, allow_nan=False)
            output.write("\n")
        os.replace(temporary, directory / filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return directory / filename


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WatchlistError("Pinned watchlist downloads must not redirect")


def fetch_pinned(url, maximum=MAX_SOURCE_BYTES, asset="plane-alert-db.csv"):
    """The pinned asset's bytes, at most `maximum`, with no redirect followed. Only this tool
    downloads; watchlists.py reads the saved cache and never fetches."""
    source_commit(url, asset)
    if not 1 <= maximum <= MAX_SOURCE_BYTES:
        raise WatchlistError("Download limit must be within the fixed 8 MiB bound")
    request = Request(url, headers={"User-Agent": "wtf-adsb-watchlist-import/1", "Accept": "text/plain"})
    with build_opener(_NoRedirect()).open(request, timeout=30) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > maximum:
            raise WatchlistError("Pinned source exceeds the download limit")
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise WatchlistError("Pinned source exceeds the download limit")
    return data


def private_directory(path):
    try:
        return private_report_directory(path)
    except ValueError as error:
        raise WatchlistError(str(error)) from error


def private_bytes(directory, filename, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=directory, prefix=".source-", delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            output.write(data)
        os.replace(temporary, directory / filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def update_cache(url, expected_sha256, output, source_date=None, fetcher=fetch_pinned):
    commit = source_commit(url)
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise WatchlistError("Expected SHA-256 must contain 64 hexadecimal characters")
    if source_date:
        try:
            parsed_date = datetime.fromisoformat(source_date.replace("Z", "+00:00"))
            if parsed_date.tzinfo is None:
                raise ValueError("Timezone required")
        except (ValueError, AttributeError) as error:
            raise WatchlistError("Source date must be an ISO timestamp with a timezone") from error
    destination = private_directory(output)
    version = private_directory(destination / commit)
    data = fetcher(url)
    catalog = parse_catalog(data, url, expected_sha256, source_date=source_date)
    license_url = url.rsplit("/", 1)[0] + "/LICENSE"
    license_data = fetcher(license_url, maximum=128 * 1024, asset="LICENSE")
    if len(license_data) > 128 * 1024:
        raise WatchlistError("License file exceeds its fixed size bound")
    license_text = license_data.decode("utf-8").lower()
    if (
        "opendatacommons.org/licenses/odbl/" not in license_text
        or "opendatacommons.org/licenses/dbcl/" not in license_text
    ):
        raise WatchlistError(
            "Pinned license differs from the reviewed ODbL/DBCL declaration; cache not changed"
        )
    catalog["source"]["license_sha256"] = hashlib.sha256(license_data).hexdigest()
    cache_bytes = (json.dumps(catalog, indent=2, allow_nan=False) + "\n").encode()
    if len(cache_bytes) > MAX_CACHE_BYTES:
        raise WatchlistError("Derived cache exceeds the fixed 32 MiB runtime bound")
    private_bytes(version, "plane-alert-db.csv", data)
    private_bytes(version, "LICENSE", license_data)
    private_bytes(version, "catalog.json", cache_bytes)
    write_private_report(
        version, {key: value for key, value in catalog.items() if key != "entries"}, "manifest.json"
    )
    # Publish the local pointer only after all source and attribution files exist.
    private_bytes(destination, "current.json", cache_bytes)
    return {
        "source_commit": commit,
        "source_sha256": catalog["source"]["sha256"],
        "downloaded_bytes": len(data),
        **catalog["statistics"],
        "private_cache": str(destination / "current.json"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", required=True, help="Allowlisted raw GitHub CSV URL pinned to a 40-character commit"
    )
    parser.add_argument("--sha256", required=True, help="Previously reviewed CSV SHA-256")
    parser.add_argument(
        "--source-date", help="Reviewed commit date, including timezone; row dates remain unknown"
    )
    parser.add_argument("--output", type=Path, default=PROJECT / ".private" / "watchlists")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        print(json.dumps(update_cache(args.url, args.sha256, args.output, args.source_date), indent=2))
    except (ValueError, OSError, UnicodeError) as error:
        parser.exit(2, f"Watchlist update stopped: {error}\n")


if __name__ == "__main__":
    main()
