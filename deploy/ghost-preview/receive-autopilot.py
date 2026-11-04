#!/usr/bin/env python3
"""Install a checked static release; public routing is managed separately."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

ROOT = Path("/opt/wtf-adsb/theme-preview")
# URLs whose content never changes (build.mjs feeds.IMMUTABLE_DIRECTORIES).
IMMUTABLE = ("event-data", "event-pages", "event-media")
# Snapshot generations: event-data/<snapshot>/ and event-pages/<snapshot>/.
GENERATIONS = ("event-data", "event-pages")
# Generations kept, the incoming one included, for readers of pages loaded
# before a release. Each holds a card per story, so the count stays bounded.
RETAINED_GENERATIONS = 12
# Event fields a release may add to an event that lacked them without correcting it.
ADDABLE_FIELDS = ("weekly_spotlight", "headline_measurements", "identity_conflict")
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_SITE_BYTES = 2 * 1024**3
MAX_FILES = 100000


def reload_preview():
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "compose.yaml"),
            "up",
            "-d",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "60",
            "preview",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=90,
    )


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path, value):
    """A durable journal must precede any rename of the current site."""
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def writer_lock():
    descriptor = os.open(ROOT / ".autopilot.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def file_hash(path):
    with path.open("rb") as content:
        return hashlib.file_digest(content, "sha256").hexdigest()


def inventory(site):
    if site.is_symlink() or not site.is_dir():
        raise ValueError("Missing or invalid site directory")
    files, size = {}, 0
    for path in sorted(site.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Site contains an unsupported file")
        if path.is_file():
            size += path.stat().st_size
            if len(files) >= MAX_FILES or size > MAX_SITE_BYTES:
                raise ValueError("Expanded release exceeds size limit")
            files[path.relative_to(site).as_posix()] = file_hash(path)
    if "index.html" not in files:
        raise ValueError("Release has no homepage")
    return files


def read_release(stream, expected, temporary):
    archive = temporary / "release.tar.gz"
    digest, size = hashlib.sha256(), 0
    with archive.open("wb") as output:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise ValueError("Release exceeds size limit")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError("Release digest mismatch")
    members, expanded = set(), 0
    with tarfile.open(archive, "r:gz") as package:
        for member in package:
            path = PurePosixPath(member.name)
            expanded += member.size
            if len(members) >= MAX_FILES or expanded > MAX_SITE_BYTES:
                raise ValueError("Expanded release exceeds size limit")
            if (
                not member.isfile()
                or member.size < 0
                or path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != member.name
                or member.name in members
                or not (member.name == "manifest.json" or member.name.startswith("site/"))
            ):
                raise ValueError("Invalid release member")
            members.add(member.name)
            target = temporary / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
    manifest = json.loads((temporary / "manifest.json").read_text())
    site = temporary / "site"
    if inventory(site) != manifest:
        raise ValueError("Release inventory mismatch")
    return site


def production_site(site):
    marker = site / "publication.json"
    if not marker.exists():
        return False
    data = json.loads(marker.read_text())
    if data not in ({"mode": "preview"}, {"mode": "production"}):
        raise ValueError("Invalid publication mode")
    return data["mode"] == "production"


def published_events(site):
    snapshot = json.loads((site / "event-snapshot.json").read_text())
    if not isinstance(snapshot, dict) or snapshot.get("mode") != "published":
        raise ValueError("Production requires a published snapshot")
    events = snapshot.get("events")
    if not isinstance(events, list) or any(
        not isinstance(event, dict)
        or not isinstance(event.get("id"), str)
        or event.get("synthetic") is not False
        for event in events
    ):
        raise ValueError("Invalid production events")
    indexed = {event["id"]: event for event in events}
    if len(indexed) != len(events):
        raise ValueError("Duplicate production events")
    return indexed


def uncorrected(previous, incoming):
    """True when every earlier public event remains and nothing in it was corrected.

    Ghost's revision time and the content revision time move without a
    correction, template prose may give way to AI prose, weekly research,
    headline measurement keys, or the identity conflict flag may be added, and
    the related story list may change in any way. Any other change, such as
    new evidence, other research, or replaced AI prose, is treated as a
    correction, as is a withdrawal.
    """

    def source(event):
        story = event.get("story")
        return story.get("source") if isinstance(story, dict) else None

    def without(event, ignored):
        return {key: value for key, value in event.items() if key not in ignored}

    for identifier, event in previous.items():
        current = incoming.get(identifier)
        if current is None:
            return False
        # Links to the registration's other stories are navigation, never a correction.
        ignored = {"revised_at", "content_revised_at", "related"}
        if source(event) == "template" and source(current) == "ai":
            ignored |= {"story", "title", "summary"}
        # Fields an earlier release did not export yet may appear without a correction.
        ignored |= {key for key in ADDABLE_FIELDS if key not in event}
        if without(event, ignored) != without(current, ignored):
            return False
    return True


def newest_generations(site, limit):
    """Names of the site's newest snapshot generations, by their newest file."""
    newest = {}
    for name in GENERATIONS:
        folder = site / name
        if folder.is_dir() and not folder.is_symlink():
            for path in folder.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    key = path.relative_to(folder).parts[0]
                    newest[key] = max(newest.get(key, 0), path.stat().st_mtime)
    return set(sorted(newest, key=lambda key: (newest[key], key), reverse=True)[:limit])


def retain_immutable(current, incoming):
    """Old snapshot URLs keep exactly the same bytes across new releases.

    The newest generations are kept (RETAINED_GENERATIONS, the incoming one
    included) with all media, and copies keep their file times, which date
    each generation.
    """
    current_public, incoming_public = production_site(current), production_site(incoming)
    if current_public and not incoming_public:
        raise ValueError("A production site cannot accept a preview release")
    if incoming_public:
        events = published_events(incoming)
        if current_public and not uncorrected(published_events(current), events):
            # Retention is safe only while prior public records are uncorrected.
            # A correction or withdrawal expires older pages, data and media.
            return
    if incoming_public and not current_public:
        # Prelaunch generations may include unpublished review data. Keep them
        # in the protected launch backup, never in the first public release.
        return
    kept = newest_generations(current, RETAINED_GENERATIONS - 1)
    for name in IMMUTABLE:
        old = current / name
        if not old.exists() and not old.is_symlink():
            continue
        if old.is_symlink() or not old.is_dir():
            raise ValueError("Invalid immutable asset directory")
        for path in sorted(old.rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("Invalid immutable asset")
            relative = path.relative_to(current)
            if name in GENERATIONS and relative.parts[1] not in kept:
                continue
            target = incoming / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif target.exists():
                if not target.is_file() or file_hash(path) != file_hash(target):
                    raise ValueError("An immutable URL has different content")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)


def sync_site(site):
    directories = [site]
    for path in site.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
            with path.open("rb") as content:
                os.fsync(content.fileno())
        else:
            directories.append(path)
    for path in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        path.chmod(0o755)
        sync_directory(path)


def clear_journal():
    (ROOT / "autopilot-install.json").unlink()
    sync_directory(ROOT)


def rollback(journal):
    """This phase also survives a crash during either rollback rename."""
    journal = {**journal, "phase": "rollback"}
    write_json(ROOT / "autopilot-install.json", journal)
    site, backup, incoming = (
        ROOT / name for name in ("site", "site-before-autopilot", "site-next-autopilot")
    )
    if backup.exists():
        if site.exists():
            if incoming.exists():
                raise ValueError("Ambiguous preview recovery state")
            site.rename(incoming)
            sync_directory(ROOT)
        backup.rename(site)
        sync_directory(ROOT)
    if site.is_symlink() or not site.is_dir():
        raise ValueError("No recoverable preview site")
    reload_preview()
    if incoming.exists():
        shutil.rmtree(incoming)
        sync_directory(ROOT)
    clear_journal()


def finish_install(journal):
    site, backup, incoming = (
        ROOT / name for name in ("site", "site-before-autopilot", "site-next-autopilot")
    )
    try:
        if incoming.exists():
            if site.exists():
                if backup.exists():
                    raise ValueError("Ambiguous preview recovery state")
                site.rename(backup)
                sync_directory(ROOT)
            elif not backup.is_dir():
                raise ValueError("No recoverable preview site")
            incoming.rename(site)
            sync_directory(ROOT)
        if not backup.is_dir() or backup.is_symlink() or inventory(site) != journal["manifest"]:
            raise ValueError("Interrupted release does not match its journal")
        reload_preview()
    except Exception:
        rollback(journal)
        raise
    receipt = {"sha256": journal["sha256"], "healthy": True, "files": len(journal["manifest"])}
    write_json(ROOT / "autopilot-release.json", receipt)
    clear_journal()
    return receipt


def recover():
    """Complete or roll back a pending install before accepting another one."""
    path = ROOT / "autopilot-install.json"
    if path.exists():
        journal = json.loads(path.read_text())
        if journal.get("version") != 1 or journal.get("phase") not in {"installing", "rollback"}:
            raise ValueError("Invalid preview install journal")
        if journal["phase"] == "rollback":
            rollback(journal)
        else:
            return finish_install(journal)
    site, backup = ROOT / "site", ROOT / "site-before-autopilot"
    # Recover an interrupted installation made by the older receiver as well.
    if not site.exists() and backup.exists():
        rollback({"version": 1, "phase": "rollback"})
    if site.is_symlink() or not site.is_dir():
        raise ValueError("Missing current preview site")
    incoming = ROOT / "site-next-autopilot"
    if incoming.exists():
        shutil.rmtree(incoming)
        sync_directory(ROOT)


def install(incoming, expected):
    retain_immutable(ROOT / "site", incoming)
    manifest = inventory(incoming)
    sync_site(incoming)
    backup = ROOT / "site-before-autopilot"
    if backup.exists():
        shutil.rmtree(backup)
        sync_directory(ROOT)
    incoming.rename(ROOT / "site-next-autopilot")
    sync_directory(ROOT)
    journal = {"version": 1, "phase": "installing", "sha256": expected, "manifest": manifest}
    write_json(ROOT / "autopilot-install.json", journal)
    return finish_install(journal)


def receive(stream, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Invalid release digest")
    with writer_lock():
        recover()
        with tempfile.TemporaryDirectory(prefix="autopilot-", dir=ROOT) as temporary:
            site = read_release(stream, expected, Path(temporary))
            return install(site, expected)


def main():
    os.umask(0o077)
    try:
        print(json.dumps(receive(sys.stdin.buffer, sys.argv[1])))
    except (OSError, ValueError, KeyError, IndexError, tarfile.TarError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Preview installation failed ({type(error).__name__}).") from None


if __name__ == "__main__":
    main()
