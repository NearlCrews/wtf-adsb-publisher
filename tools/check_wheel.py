"""Check package metadata and run the base wheel outside the checkout."""

import email
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    wheels = list((ROOT / "dist").glob("wtf_adsb_publisher-*.whl"))
    sources = list((ROOT / "dist").glob("wtf_adsb_publisher-*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise SystemExit("Build exactly one publisher wheel and source archive before this check")
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(wheel.read(metadata_path))
        assert metadata["License-Expression"] == "MIT"
        assert "Private :: Do Not Upload" not in metadata.get_all("Classifier", [])
        assert "Homepage, https://wtf-adsb.com" in metadata.get_all("Project-URL", [])
        assert metadata["Description-Content-Type"] == "text/markdown"
        assert metadata.get_payload().strip() == (ROOT / "README.md").read_text().strip()
        licenses = metadata_path.rsplit("/", 1)[0] + "/licenses/"
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "contracts/ENGINE-LICENSE"):
            assert wheel.read(licenses + name) == (ROOT / name).read_bytes()
        for source in (ROOT / "contracts").glob("*.schema.json"):
            assert wheel.read("wtf_adsb_publisher/contracts/" + source.name) == source.read_bytes()
    with tarfile.open(sources[0]) as source:
        source_names = source.getnames()
        base = source_names[0].split("/", 1)[0]
        for name in (
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "CONTRIBUTING.md",
            "DEVELOPMENT.md",
            "AGENTS.md",
            "CLAUDE.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
            "CHANGELOG.md",
            "SUPPORT.md",
            "AUTHORS.md",
            "docs/README.md",
            "docs/operations.md",
            "docs/migration.md",
            "docs/setup-interview.md",
            "tools/setup.py",
            "tools/run_autopilot.py",
            "themes/wtf-adsb/LICENSE",
        ):
            assert base + "/" + name in source_names, f"Source archive lacks {name}"
        names.extend(source_names)
    for name in names:
        path = Path(name)
        assert not {".private", ".venv", "state", "artifacts", "node_modules", "__pycache__"}.intersection(
            path.parts
        )
        assert path.suffix not in {".sqlite", ".sqlite3", ".db", ".pem", ".key"}
    with tempfile.TemporaryDirectory(prefix="wtf-publisher-wheel-") as temporary:
        work = Path(temporary)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("WTF_", "AI_", "OPENAI_", "PYTHONPATH"))
        }
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(work / "venv")],
            check=True,
            env=environment,
            capture_output=True,
        )
        python = work / "venv/bin/python"
        subprocess.run(
            ["uv", "pip", "install", "--offline", "--python", str(python), str(wheels[0])],
            check=True,
            env=environment,
            capture_output=True,
        )
        shutil.copyfile(ROOT / "examples/change-batch.example.json", work / "example.json")
        code = r"""
import contextlib, importlib.util, io, json, socket
from pathlib import Path
import wtf_adsb_publisher
from wtf_adsb_publisher import budget_store, cli, plugins, wire
package = Path(wtf_adsb_publisher.__file__).parent
assert package.is_relative_to(Path.cwd() / 'venv')
assert importlib.util.find_spec('wtf_adsb_engine') is None
def denied(*args, **kwargs):
    raise AssertionError('Installed-wheel smoke test attempted network I/O')
socket.socket.connect = denied
wire.validate_batch(wire.loads(Path('example.json').read_bytes()), allow_synthetic=True)
assert (package / 'prompts/humanizer.md').read_text()
assert (package / 'prompts/humanizer.LICENSE').read_text()
assert json.loads((package / 'data/topics.json').read_text())
for role, name in plugins.DEFAULTS.items():
    assert name in plugins.discover(role)
    assert plugins.load(role, {}).descriptor.role == role
with contextlib.redirect_stdout(io.StringIO()):
    cli.main(['plugins'])
    cli.main(['init', '--state-dir', 'state', '--available-from', '2026-10-01'])
with budget_store.connect('state/budget.sqlite3') as database:
    assert database.execute('SELECT COUNT(*) FROM ai_runs').fetchone()[0] == 0
assert Path('state').stat().st_mode & 0o777 == 0o700
assert Path('state/budget.sqlite3').stat().st_mode & 0o777 == 0o600
print(json.dumps({'installed_wheel': True, 'independent_plugins': 6, 'offline': True}))
"""
        result = subprocess.run(
            [str(python), "-I", "-c", code],
            cwd=work,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        print(json.dumps(json.loads(result.stdout)))


if __name__ == "__main__":
    main()
