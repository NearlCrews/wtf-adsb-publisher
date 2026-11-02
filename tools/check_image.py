"""Exercise the built publisher image with synthetic inputs and no network."""

import argparse
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    ".private",
    "private",
    "secrets",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
# Reference files the operator supplies at run time (reference.OPERATOR_SUPPLIED):
# their redistribution terms are unresolved, so .dockerignore keeps any local
# copy out of the image and the runtime check asserts it is absent.
OPERATOR_FILES = frozenset(
    {
        "reference-aircraft-types-v1.json",
        "reference-identity-v1.json",
        "reference-notable-variants-v1.json",
    }
)


def source_digest(root):
    """Bind this check to the source and assets actually copied into the image."""
    for directory in (root, root / "deploy", root / "deploy/container", root / "deploy/ghost-preview"):
        if directory.is_symlink():
            raise ValueError("Image sources must not contain symbolic links")
    paths = [
        root / name
        for name in (
            "Dockerfile",
            ".dockerignore",
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "package.json",
            "package-lock.json",
            "deploy/container/entrypoint.py",
            "deploy/container/build_metadata.py",
            "deploy/ghost-preview/routes.yaml",
        )
    ]
    for name in ("src", "contracts", "examples", "tools", "themes", "tests"):
        source = root / name
        if source.is_symlink():
            raise ValueError("Image sources must not contain symbolic links")
        for directory, children, files in os.walk(source, followlinks=False):
            children[:] = [child for child in children if child not in EXCLUDED]
            if any((Path(directory) / child).is_symlink() for child in children):
                raise ValueError("Image sources must not contain symbolic links")
            paths.extend(
                Path(directory) / filename
                for filename in files
                if not filename.startswith(".env")
                and filename not in OPERATOR_FILES
                and ".sqlite" not in filename
                and Path(filename).suffix
                not in {".pyc", ".pyo", ".db", ".pem", ".key", ".log", ".sync-merge"}
            )
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError("Image sources must not contain symbolic links")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run(image, args=(), *, entrypoint=None, timeout=180):
    name = "wtf-publisher-check-" + uuid.uuid4().hex
    command = [
        "docker",
        "run",
        "--name",
        name,
        "--rm",
        "--pull=never",
        "--init",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--memory=4g",
        "--shm-size=256m",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
        "--tmpfs",
        "/app/artifacts/ghost-theme:rw,nosuid,nodev,size=512m,uid=1000,gid=1000,mode=0700",
    ]
    if entrypoint:
        command.extend(("--entrypoint", entrypoint))
    try:
        result = subprocess.run([*command, image, *args], capture_output=True, text=True, timeout=timeout)
    finally:
        # A killed Docker client can leave its container running. Reap only the
        # unique container created by this invocation, including timeout paths.
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"Image check failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    return result.stdout


RUNTIME = r"""
import importlib.metadata, importlib.util, json, os, platform, pwd, subprocess, sys
from pathlib import Path
sys.path.insert(0, '/app/tools')
from check_image import OPERATOR_FILES, source_digest
from wtf_adsb_publisher import reference
assert {reference.FILES[name][0] for name in reference.OPERATOR_SUPPLIED} == OPERATOR_FILES
shipped = [str(path) for top in ('/app', '/opt/venv') for path in Path(top).rglob('*') if path.name in OPERATOR_FILES]
assert not shipped, shipped
assert reference.reference_dir() is None and reference.dataset('identity') is None
assert reference.dataset('limits') is not None
assert platform.python_version() == '3.14.9'
assert os.getuid() == os.getgid() == 1000
assert pwd.getpwuid(os.getuid()).pw_name == 'publisher'
assert pwd.getpwuid(os.getuid()).pw_dir == os.environ['HOME'] == '/home/publisher'
assert sys.executable == '/opt/venv/bin/python'
assert importlib.metadata.version('jsonschema') == '4.26.0'
assert importlib.metadata.version('packaging') == '26.3'
assert importlib.util.find_spec('wtf_adsb_engine') is None
assert importlib.util.find_spec('ruff') is None
assert subprocess.check_output(['node', '--version'], text=True).strip() == 'v24.22.1'
assert subprocess.check_output(['npm', '--version'], text=True).strip() == '12.2.0'
for name, version in [('handlebars', '4.7.10'), ('playwright', '1.64.1')]:
    path = Path('/app/tools/ghost-theme/node_modules') / name / 'package.json'
    assert json.loads(path.read_text())['version'] == version
assert not Path('/app/tools/ghost-theme/node_modules/gscan').exists()
assert not Path('/app/tools/ghost-theme/node_modules/@axe-core').exists()
assert not Path('/app/node_modules').exists()
assert not Path('/app/.private').exists()
assert not Path('/app/.venv').exists()
assert Path('/app/src/wtf_adsb_publisher/prompts/humanizer.LICENSE').is_file()
assert Path('/app/contracts/ENGINE-LICENSE').is_file()
assert Path('/app/themes/wtf-adsb/LICENSE').is_file()
assert Path('/app/tools/compare_cutover.py').is_file()
metadata = json.loads(Path('/usr/local/share/wtf-adsb-publisher/build.json').read_text())
assert metadata['source_sha256'] == source_digest(Path('/app'))
assert metadata['python_packages'] and metadata['os_packages'] and metadata['npm_dependencies']
subprocess.run(['python', '-m', 'pip', 'check'], check=True, capture_output=True)
ssh = subprocess.check_output(['ssh', '-G', 'fixture.invalid'], text=True, stderr=subprocess.DEVNULL)
assert 'user publisher\n' in ssh
assert '/home/publisher/.ssh/' in ssh
try:
    Path('/app/unexpected-write').write_text('refuse')
except OSError:
    pass
else:
    raise AssertionError('Application source is writable')
Path('/app/artifacts/ghost-theme/permission-check').write_text('writable')
print(json.dumps({'source_digest': source_digest(Path('/app')), 'uid': os.getuid(), 'offline': True}))
"""

GUARDS = r"""
import os, subprocess, sys
from pathlib import Path
entrypoint = [sys.executable, '/app/deploy/container/entrypoint.py']
directory = Path('/tmp/control')
directory.mkdir(mode=0o700)
hold = directory / 'activation.hold'
environment = {**os.environ, 'PUBLISHER_ACTIVATION_HOLD': str(hold)}
def invoke(alias, *args):
    return subprocess.run([*entrypoint, alias, *args], env=environment, capture_output=True)
hold.write_text('held')
for alias in ('publish', 'write', 'weekly'):
    assert invoke(alias, '--config', '/does-not-exist').returncode == 75
    assert invoke(alias, '--help').returncode == 0
hold.unlink()
hold.symlink_to(directory / 'missing')
assert invoke('publish').returncode == 75
hold.unlink()
directory.chmod(0)
try:
    assert invoke('publish').returncode == 75
finally:
    directory.chmod(0o700)
assert invoke('publish', '--unknown-argument').returncode == 2
assert invoke('invalid-command').returncode == 2
assert invoke('admin', '--help').returncode == 0
print('Entrypoint arguments, activation hold, permission failures and exit statuses passed.')
"""

PIPELINE = r"""
import contextlib, hashlib, io, json, os, socket, subprocess
from pathlib import Path
from wtf_adsb_publisher import cli, ingest, wire
os.umask(0o077)
def denied(*args, **kwargs):
    raise AssertionError('Synthetic pipeline attempted a network connection')
socket.socket.connect = denied
state = Path('/tmp/state')
with contextlib.redirect_stdout(io.StringIO()):
    cli.main(['init', '--state-dir', str(state), '--available-from', '2026-10-01'])
assert state.stat().st_mode & 0o777 == 0o700
assert (state / 'budget.sqlite3').stat().st_mode & 0o777 == 0o600
batch = wire.loads(Path('/app/examples/change-batch.example.json').read_bytes())
raw = wire.canonical(batch)
feed = Path('/tmp/feed')
wire.atomic_bytes(feed / 'batches/1-1.json', raw)
manifest = {
    'schema': 'wtf-adsb.manifest', 'schema_version': wire.VERSION, 'profile': wire.PROFILE,
    'stream_id': batch['stream_id'], 'generation': batch['generation'], 'head_sequence': 1,
    'first_sequence': 1, 'updated_at': batch['generated_at'],
    'health': {'ready': True, 'complete': True, 'mode': 'replay',
               'last_receiver_poll': None, 'analysis_watermark': None},
    'batches': [{'path': 'batches/1-1.json', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                 'sequence_start': 1, 'sequence_end': 1}],
}
wire.atomic_bytes(feed / 'manifest.json', wire.canonical(manifest))
with contextlib.redirect_stdout(io.StringIO()):
    for _ in range(2):
        cli.main(['ingest', '--feed', str(feed), '--database', str(state / 'inbox.sqlite3'), '--allow-synthetic'])
    cli.main(['status', '--database', str(state / 'inbox.sqlite3')])
imported = ingest.read_state(state / 'inbox.sqlite3')
assert imported['cursor'] == 1 and len(imported['observations']) == 1
assert (state / 'inbox.sqlite3').stat().st_mode & 0o777 == 0o600
work = Path('/app/artifacts/ghost-theme/smoke')
work.mkdir(mode=0o700)
snapshot = subprocess.check_output([
    'python', 'tools/ghost-theme/site_fixtures.py', '--mode', 'review', '--no-extra',
    '--media', str(work / 'media'),
], text=True)
snapshot_path = work / 'review.json'
snapshot_path.write_text(snapshot)
assert json.loads(snapshot)['mode'] == 'review'
subprocess.run([
    'node', 'tools/ghost-theme/build.mjs', '--snapshot', str(snapshot_path),
    '--media', str(work / 'media/manifest.json'), '--output', str(work / 'site'),
], check=True, capture_output=True, text=True)
assert (work / 'site/index.html').is_file()
assert any((work / 'site/event-media').rglob('*.svg'))
# Review sites omit social images. Exercise the real production renderer
# independently using constructed artwork and the bundled source font.
subprocess.run(['node', '--input-type=module', '-e', '''
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { executablePath } from './tools/ghost-theme/browser.mjs';
import { renderSocialCards } from './tools/ghost-theme/social-cards.mjs';
const pin = JSON.parse(await fs.readFile('tools/ghost-theme/browser-version.json', 'utf8'));
assert(execFileSync(executablePath, ['--version'], {encoding:'utf8'}).trim().endsWith(' ' + pin.version));
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800"><rect width="1200" height="800" fill="#315848"/></svg>';
const cards = await renderSocialCards([
  {id:'card', kind:'card', svg}, {id:'tile', kind:'tile', svg}, {id:'logo', kind:'logo'}
], {cache:'/app/artifacts/ghost-theme/smoke/cold-cache', font:await fs.readFile('tools/ghost-theme/fonts/Newsreader.ttf')});
assert.equal(cards.size, 3);
assert.equal(cards.get('card').data.subarray(0,8).toString('hex'), '89504e470d0a1a0a');
assert.equal(cards.get('tile').data.subarray(0,2).toString('hex'), 'ffd8');
assert.equal(cards.get('card').width, 1200);
assert.equal(cards.get('tile').width, 720);
'''], check=True, capture_output=True, text=True)
print(json.dumps({'inbox_cursor': imported['cursor'], 'duplicate_delivery': 'unchanged',
                  'cold_browser_render': True, 'synthetic_site': True, 'private_modes': True}))
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="wtf-adsb-publisher:local")
    args = parser.parse_args(argv)
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True
    ).strip()
    assert image.startswith("sha256:"), "Expected a local immutable image ID"
    assert "Usage: publisher" in run(image)
    for alias in ("publish", "write", "weekly", "admin"):
        assert "usage:" in run(image, (alias, "--help")).lower()
    runtime = json.loads(run(image, ("-c", RUNTIME), entrypoint="python"))
    assert runtime["source_digest"] == source_digest(ROOT), "Image sources differ from this checkout; rebuild"
    print(json.dumps({"image_id": image, **runtime}), flush=True)
    print(run(image, ("-c", GUARDS), entrypoint="python").strip(), flush=True)
    print(run(image, ("-c", PIPELINE), entrypoint="python", timeout=600).strip(), flush=True)
    print("Publisher image checks passed without network access or host source mounts.")


if __name__ == "__main__":
    main()
