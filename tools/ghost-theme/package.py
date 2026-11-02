#!/usr/bin/env python3
"""Package only native Ghost theme files, with a repeatable ZIP manifest."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
THEME = ROOT / "themes" / "wtf-adsb"
OUTPUT = ROOT / "artifacts" / "ghost-theme"
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import private_terms  # noqa: E402


def main() -> None:
    """Build a versioned archive without preview fixtures or development tools."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="Validated published snapshot for native weekly picks")
    args = parser.parse_args()
    # The ZIP is published. The browser check applies the same expression to rendered pages.
    private = re.compile(private_terms.pattern().encode(), re.I)
    replacements = {}
    routes = (ROOT / "deploy/ghost-preview/routes.yaml").read_text()
    if args.snapshot:
        from wtf_adsb_publisher.public_events import read_json
        from wtf_adsb_publisher.weekly_spotlight import native_archive_filter, native_partials

        snapshot = read_json(args.snapshot)
        replacements = {name: value.encode() for name, value in native_partials(snapshot).items()}
        routes = routes.replace(
            "filter: tag:hash-weekly-unconfigured", "filter: " + json.dumps(native_archive_filter(snapshot))
        )
    package = json.loads((THEME / "package.json").read_text())
    name = f"wtf-adsb-{package['version']}.zip"
    output = OUTPUT / name
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sources = sorted(
        [THEME / "package.json", THEME / "LICENSE", *THEME.glob("*.hbs")]
        + list((THEME / "partials").rglob("*.hbs"))
        + [p for p in (THEME / "assets").rglob("*") if p.is_file()]
    )
    # Read and check every file first: a refusal must not leave a partial archive behind.
    files = []
    for source in sources:
        if source.is_symlink() or THEME not in source.resolve().parents:
            raise ValueError(f"Theme file escapes the source directory: {source}")
        relative = source.relative_to(THEME).as_posix()
        data = replacements.get(relative, source.read_bytes())
        if private.search(data):
            raise ValueError(f"Theme file names a private host, path, or receiver field: {relative}")
        files.append((relative, data))
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for relative, data in files:
            entry = ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, data)
    result = {
        "archive": name,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "routes_sha256": hashlib.sha256(routes.encode()).hexdigest(),
        "files": [{"path": relative, "sha256": hashlib.sha256(data).hexdigest()} for relative, data in files],
    }
    (OUTPUT / "theme-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    (OUTPUT / "routes.yaml").write_text(routes)
    print(f"Packaged {len(files)} files: {output}")


if __name__ == "__main__":
    main()
