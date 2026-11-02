"""Record image build inputs and installed dependency versions."""

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from check_image import source_digest  # noqa: E402


def output(*command):
    return subprocess.check_output(command, text=True).strip()


def main():
    target = Path("/usr/local/share/wtf-adsb-publisher/build.json")
    metadata = {
        "schema_version": 1,
        "project": "wtf-adsb-publisher",
        "version": importlib.metadata.version("wtf-adsb-publisher"),
        "source_sha256": source_digest(ROOT),
        "lock_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("uv.lock", "package-lock.json", "tools/ghost-theme/package-lock.json")
        },
        "runtimes": {
            "python": platform.python_version(),
            "node": output("node", "--version"),
            "npm": output("npm", "--version"),
            "uv_builder": output("uv", "--version"),
            "browser": json.loads((ROOT / "tools/ghost-theme/browser-version.json").read_text()),
        },
        "python_packages": dict(
            sorted(
                (distribution.metadata["Name"], distribution.version)
                for distribution in importlib.metadata.distributions()
            )
        ),
        "os_packages": dict(
            row.split("\t", 1)
            for row in output("dpkg-query", "--show", "--showformat=${Package}\t${Version}\n").splitlines()
        ),
        "npm_dependencies": json.loads(
            output(
                "npm", "--prefix", str(ROOT / "tools/ghost-theme"), "list", "--omit=dev", "--all", "--json"
            )
        ),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    target.chmod(0o644)


if __name__ == "__main__":
    main()
