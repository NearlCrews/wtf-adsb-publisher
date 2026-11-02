#!/usr/bin/env python3
"""Install the pinned stable Chrome for Testing binary without changing host packages."""

import argparse
import hashlib
import json
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

TOOLS = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, help="Use an already downloaded archive; checksum still required"
    )
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        parser.exit(1, "The pinned browser is for Linux x64; use CHROMIUM_PATH for another platform.\n")
    pin = json.loads((TOOLS / "browser-version.json").read_text())

    def verify_archive(archive):
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != pin["sha256"]:
            parser.exit(1, "Browser archive checksum does not match its pin.\n")

    if args.archive:
        verify_archive(args.archive)
    cache = TOOLS / "node_modules/.cache/chrome-for-testing"
    destination = cache / pin["version"]
    executable = destination / "chrome-linux64/chrome"
    if executable.is_file():
        version = subprocess.check_output([str(executable), "--version"], text=True).strip()
        if version.split()[-1] != pin["version"]:
            parser.exit(1, "The cached browser does not match its version pin.\n")
        print(version)
        return
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=cache) as staging:
        staging = Path(staging)
        archive = args.archive or staging / "chrome.zip"
        if not args.archive:
            print(f"Downloading Chrome for Testing {pin['version']}...", flush=True)
            with urllib.request.urlopen(pin["url"], timeout=60) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            verify_archive(archive)
        extracted = staging / "extracted"
        with ZipFile(archive) as package:
            for member in package.infolist():
                relative = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or relative.parts[0] != "chrome-linux64"
                    or stat.S_ISLNK(mode)
                ):
                    parser.exit(1, "Browser archive contains an unexpected path.\n")
                path = Path(package.extract(member, extracted))
                path.chmod(0o755 if member.is_dir() or mode & 0o111 else 0o644)
        binary = extracted / "chrome-linux64/chrome"
        version = subprocess.check_output([str(binary), "--version"], text=True).strip()
        if version.split()[-1] != pin["version"]:
            parser.exit(1, "Downloaded browser does not report the pinned version.\n")
        extracted.rename(destination)
    print(version)


if __name__ == "__main__":
    main()
