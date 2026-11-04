#!/usr/bin/env python3
"""Publish qualifying observations and refresh the configured website release.

--no-deploy publishes to Ghost and writes the snapshot and status, but builds
and sends no release. --dry-run simulates one pass against a saved Ghost
inventory: it saves no state, makes no Ghost, SSH, AI or photo API request, and
builds the would-be site into --output with the release guards. Like a real
pass, it can add social cards and WebP copies of cached maps to the build caches.
A pass that deploys first refreshes the private Planespotters photo cache.
--reproject reports which saved records a change in the evidence code would
otherwise draft, and with --apply revises them in place (autopilot.reproject).
--refresh-entities marks the cached Wikidata answers for watchlist entry links
stale, so the next passes look each one up again while stories keep the old
answer until a fresh one replaces it (entity_context.refresh).
"""

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# Airframe photos from the Planespotters API; its rules are in the module.
sys.path.insert(1, str(ROOT / "tools/ghost-theme"))
import airframe_photos as airframe

from wtf_adsb_publisher import (
    autopilot,
    entity_context,
    plugins,
    private_terms,
    site_identity,
    site_origin,
    story_urls,
    weekly_research,
)
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher import reference as reference_data
from wtf_adsb_publisher.ghost_client import GhostError

LOG = logging.getLogger("discoveries.run_autopilot")


def command(args, *, input=None):
    result = subprocess.run(args, cwd=ROOT, input=input, capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError("Autopilot subprocess failed: " + Path(args[0]).name)
    return result.stdout


def release_basemaps(directory):
    """The shared map cache for a real release: downloads allowed, WebP embedding on.

    The cache class lives in the theme tools' basemap module, which the release
    path imports directly; the media module no longer re-exports it.
    """
    import basemap

    return basemap.BasemapCache(directory, fetch=True, webp=True)


def media_module():
    directory = ROOT / "tools/ghost-theme"
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location("event_media", directory / "event-media.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_digest(paths):
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        if path.is_file():
            digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def privacy_guard(config):
    private = re.compile(private_terms.pattern(config).encode(), re.I)

    def check(content):
        if private.search(content):
            raise ValueError("Private text found in publication")

    return check


# Compressed images and fonts the build emits, by extension and file signature. Their
# bytes are not text: a short private name such as "rssi" turns up in them by chance,
# and a card that matched would stop every later release. They are not scanned; the
# SVG sources of the cards and maps are text and are. A file whose bytes do not carry
# its extension's signature is scanned like any other.
BINARY_MEDIA = {
    ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
    ".jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
    ".gif": lambda data: data.startswith((b"GIF87a", b"GIF89a")),
    ".webp": lambda data: data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    ".avif": lambda data: data[4:12] in {b"ftypavif", b"ftypavis"},
    ".ico": lambda data: data.startswith(b"\x00\x00\x01\x00"),
    ".woff": lambda data: data.startswith(b"wOFF"),
    ".woff2": lambda data: data.startswith(b"wOF2"),
}


# Article pages, the only pages an airframe photo may appear in.
STORY_PAGE = re.compile(r"stories/[a-z0-9-]+/index\.html")


def binary_media(path, content):
    """Whether a release file is a compressed image or font, which the text scan skips."""
    signature = BINARY_MEDIA.get(Path(path).suffix.lower())
    return bool(signature and signature(content))


def generate_media(snapshot, ledger, destination, previous=None, *, seed=False, basemaps=None):
    """Retain unchanged historical maps; render new evidence without paid APIs."""
    module = media_module()
    assets = destination / "event-media"
    assets.mkdir(parents=True, exist_ok=True)
    cached = public.read_json(previous) if previous and Path(previous).exists() else {"events": {}}
    records = {record["id"]: record for record in ledger["records"]}
    renderer = source_digest(
        [
            Path(__file__),
            *(ROOT / "tools/ghost-theme").glob("*.py"),
            *(ROOT / "src/wtf_adsb_publisher").glob("*.py"),
            *(ROOT / "tools/ghost-theme/data").rglob("*"),
            *(ROOT / "src/wtf_adsb_publisher/data").rglob("*"),
        ]
    )
    # WebP backgrounds change every track image, so they are their own mode.
    background = "offline-vector"
    if basemaps is not None:
        background = "usgs-topo-webp" if basemaps.webp else "usgs-topo"
    compatible = (seed or cached.get("renderer_sha256") == renderer) and cached.get(
        "background"
    ) == background
    manifest = {
        "schema_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
        "renderer_sha256": renderer,
        "background": background,
        "events": {},
    }
    for event in snapshot["events"]:
        old = cached["events"].get(event["id"])
        if compatible and old and old["evidence_version"] == event["evidence_version"]:
            sources = [old.get(key) for key in ("src", "replaySrc")]
            sources += [figure.get("src") for figure in old.get("figures", [])]
            for src in sources:
                if src:
                    path = Path(previous).parent / "event-media" / Path(src).name
                    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != path.stem:
                        raise ValueError("Invalid cached media")
                    target = assets / path.name
                    if target.resolve() != path.resolve():
                        shutil.copyfile(path, target)
            manifest["events"][event["id"]] = old
            continue
        automatic = records[event["id"]].get("automation", {})
        finding = automatic.get("source", {})
        raw = finding.get("evidence", {})
        image = module.speed_image(event, finding) if event["kind"] == "outlier_gs" and finding else None
        if event.get("subjectless"):
            # A subjectless story's aircraft are examples: no track map, only its day summary.
            image = module.summary_image(event)
        parts, others = [], []
        if image is None:
            # A TCAS episode's own samples are its advisory messages; its positions are the
            # context samples around them.
            keys = ("maneuver_samples", "pattern_samples", "track")
            if event["kind"] == "tcas_ra":
                keys = ("context_samples", *keys)
            points = next((raw[key] for key in keys if raw.get(key)), [])
            parts = (
                automatic.get("media_parts")
                if automatic.get("media_evidence_version") == event["evidence_version"]
                else None
            )
            start, end = module.instant(event["observed_start"]), module.instant(event["observed_end"])
            # Supplied segments with no drawable line, such as an advisory's, which the engine
            # cuts from its message points, give way to the evidence's own positions.
            if not isinstance(parts, list) or not any(
                isinstance(part, list) and len(part) >= 2 for part in parts
            ):
                parts = module.segments(points, start, end)
            if event["kind"] in module.SECOND_TRACK_KINDS:
                others = module.participant_tracks(raw, start, end, event["aircraft"].get("icao_address"))
            image = module.track_image(event, parts, analysis_window=False, basemaps=basemaps, others=others)
        entry = module.store_image(image or module.summary_image(event), assets, event)
        if profile := module.profile_image(event, parts, others):
            entry["figures"] = [module.store_image(profile, assets, event)]
        manifest["events"][event["id"]] = entry
    public.write_json_atomic(destination / "manifest.json", manifest, private=True)
    return destination / "manifest.json"


def airframe_photos(snapshot, config, *, fetch):
    """{event id: photo} from the private Planespotters cache, refreshed first with `fetch`, and a status.

    Off unless the config sets "airframe_photos": true, which waits for the
    public proxy's img-src to allow the image host (docs/production-launch.md).
    Turned off, a pass that may fetch deletes the cache. A failed request never
    stops a pass: a story without a fresh response has no airframe photo.
    """
    cache = airframe.PhotoCache(Path(config["state_dir"]) / "planespotters", fetch=fetch)
    if config.get("airframe_photos") is not True:
        if fetch:
            cache.prune(everything=True)
        return {}, {"enabled": False}
    photos = airframe.story_photos(snapshot, cache)
    if photos:
        # Third-party text that trips the privacy guard costs that photo, not the release.
        check_private = privacy_guard(config)
        for key, photo in list(photos.items()):
            try:
                check_private(json.dumps(photo, ensure_ascii=False).encode())
            except ValueError:
                del photos[key]
    return photos, {
        "shown": len(photos),
        "requests": cache.requests,
        **({"failure": cache.failure} if cache.failure else {}),
    }


def build_site(
    snapshot_path,
    snapshot,
    ledger,
    config,
    work,
    *,
    previous=None,
    seed=False,
    basemaps=None,
    photos=None,
    integrations=None,
):
    """Media, the static site and the release guards; returns the site and its file hashes.

    The guards stop a release that holds private text, loads a resource from
    another origin (other than an airframe photo's image in its figure) or names
    Planespotters outside a page.
    """
    if integrations is None:
        integrations = plugins.preflight(config, ("renderer",))
    media = integrations["renderer"].render(
        generate_media, snapshot, ledger, work / "media", previous, seed=seed, basemaps=basemaps
    )
    photo_file = work / "airframe-photos.json"
    public.write_json_atomic(photo_file, {"photos": photos or {}}, private=True)
    site = work / "site"
    # The receiver retains immutable generations. Upload only this generation
    # rather than copying the growing public history over SSH every time.
    if site.exists():
        shutil.rmtree(site)
    try:
        command(
            [
                "node",
                "tools/ghost-theme/build.mjs",
                "--snapshot",
                str(snapshot_path),
                "--media",
                str(media),
                "--airframe-photos",
                str(photo_file),
                "--output",
                str(site),
                *(["--production"] if config.get("production", False) else []),
            ]
        )
    finally:
        # The photo list lives no longer than the build that reads it.
        photo_file.unlink(missing_ok=True)
    check_private = privacy_guard(config)
    files = {}
    for path in sorted(site.rglob("*")):
        if path.is_symlink():
            raise ValueError("Release contains a symlink")
        if not path.is_file():
            continue
        content = path.read_bytes()
        name = path.relative_to(site).as_posix()
        if not binary_media(path, content):
            check_private(content)
            if airframe.release_violations(
                content,
                page=name.endswith(".html"),
                story=bool(STORY_PAGE.fullmatch(name)),
                style=name.endswith((".css", ".svg")),
            ):
                raise ValueError("Third-party resource or photo data in publication")
        files[name] = hashlib.sha256(content).hexdigest()
    if not files or "index.html" not in files:
        raise ValueError("Release has no homepage")
    return site, files


def deploy(work, site, files, config, state):
    manifest = work / "manifest.json"
    public.write_json_atomic(manifest, files, private=True)
    archive = work / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        package.add(manifest, arcname="manifest.json")
        for name in files:
            package.add(site / name, arcname="site/" + name)
    content = archive.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    host = config["preview_ssh_host"]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]*", host):
        raise ValueError("Invalid SSH host")
    response = command(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            host,
            "sudo",
            "-n",
            "python3",
            "/opt/wtf-adsb/theme-preview/receive-autopilot.py",
            digest,
        ],
        input=content,
    )
    receipt = json.loads(response)
    if receipt.get("sha256") != digest or receipt.get("healthy") is not True:
        raise ValueError("Preview did not confirm the release")
    state["release"] = receipt


def build_release(snapshot, ledger, config, state, *, photos=None, integrations=None):
    if integrations is None:
        integrations = plugins.preflight(config, ("renderer", "deployer"))
    work = ROOT / "artifacts/ghost-theme/autopilot"
    work.mkdir(parents=True, exist_ok=True)
    snapshot_path = Path(config["state_dir"]) / "snapshot.json"
    public.write_json_atomic(snapshot_path, snapshot, private=True)
    previous = work / "media/manifest.json"
    seed = False
    if not previous.exists() and config.get("seed_media"):
        previous = Path(config["seed_media"])
        seed = True
    basemaps = release_basemaps(ROOT / "artifacts/ghost-theme/basemap-cache")
    site, files = build_site(
        snapshot_path,
        snapshot,
        ledger,
        config,
        work,
        previous=previous,
        seed=seed,
        basemaps=basemaps,
        photos=photos,
        integrations=integrations,
    )
    integrations["deployer"].deploy(deploy, work, site, files, config, state)


COVERAGE_WARNINGS = ("coverage_gap", "coverage_overlap", "coverage_unknown")
COVERAGE_TOLERANCE = 120


def coverage(ledger, live_since, frozen_through=None):
    """Compare the live window's start with the end of the frozen history.

    `frozen_through` is the frozen corpus cutoff from the configuration. Without
    it, the newest frozen record's observed end stands in. A gap leaves
    observations in neither window; an overlap can consider them twice. This
    returns a warning name and details, or None, and never stops publication.
    """
    if frozen_through is None:
        ends = []
        for record in ledger.get("records", []):
            if (record.get("automation") or {}).get("source_kind") != "frozen":
                continue
            try:
                ends.append(public._instant(record["evidence"]["observation"]["end_utc"]))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        if not ends:
            return None
        boundary, basis = max(ends), "newest_frozen_record"
    else:
        boundary, basis = public._instant(frozen_through), "frozen_through"
    seconds = (live_since - boundary).total_seconds()
    if abs(seconds) <= COVERAGE_TOLERANCE:
        return None
    return "coverage_gap" if seconds > 0 else "coverage_overlap", {
        "frozen_through_utc": public.utc(boundary.isoformat()),
        "live_since_utc": public.utc(live_since.isoformat()),
        "seconds": round(abs(seconds)),
        "basis": basis,
    }


def story_profiles(state_dir):
    """The sourced type profiles stories may cite, and whether the saved file was unreadable.

    New stories cite the profiles the writer saved, as the bootstrap's do. An
    unreadable file only costs that background; it never stops publication.
    """
    try:
        return weekly_research.story_profiles(weekly_research.load_profiles(state_dir)), False
    except (OSError, ValueError):
        return {}, True


def publication_pass(
    config, ledger, status, connect, persist, at, *, simulate=False, integrations=None, lookup_entities=True
):
    """Collect, stage and publish once; coverage warnings go into `status`.

    `connect()` returns the Ghost client after staging. The ledger and status
    are changed in place; `persist` decides whether anything is saved. With
    `lookup_entities`, a few uncached watchlist entry links are looked up on
    Wikidata first, the candidates' and then those of records already in the
    ledger; a dry run only reads the cache.
    """
    now = public._instant(at).timestamp()
    live_since = public._instant(config["live_since"])
    for name in COVERAGE_WARNINGS:
        status.pop(name, None)
    try:
        warning = coverage(ledger, live_since, config.get("frozen_through"))
    except (ValueError, TypeError, KeyError, AttributeError):
        # A failed check is reported; it never blocks publication.
        warning = (
            "coverage_unknown",
            {"reason": "live_since could not be compared with the frozen history"},
        )
    if warning:
        status[warning[0]] = warning[1]
    from wtf_adsb_publisher.feed_adapter import apply_retractions, feed_fresh, unexported_norms

    if integrations is None:
        integrations = plugins.preflight(config, ("reader", "policy"))
    feed = integrations["reader"].read(config)
    synthetic_held = feed.get("synthetic") is True and not simulate
    if synthetic_held:
        status["feed_synthetic"] = True
    else:
        status.pop("feed_synthetic", None)
    replay_allowed = (
        feed.get("health", {}).get("mode") != "replay" or config.get("allow_replay_publication") is True
    )
    if (
        not status.get("feed_ingest_error")
        and not synthetic_held
        and replay_allowed
        and feed_fresh(feed, now, config.get("max_feed_age_seconds", 300))
    ):
        candidates = integrations["policy"].select(feed, now, since=live_since.timestamp(), ledger=ledger)
        status.pop("feed_stale", None)
    else:
        # Verified real withdrawals still apply while collection is unavailable.
        if not synthetic_held:
            apply_retractions(feed, ledger, at=at)
        candidates = []
        status["feed_stale"] = True
        for record in ledger["records"]:
            if not record.get("automation", {}).get("released_at"):
                autopilot.block(record, "feed_stale")
    # A speed story staged from a 1.0.0 observation keeps no norm; the pass says which.
    unexported = unexported_norms(feed, candidates)
    if unexported:
        status["type_norm_unexported"] = {"stories": unexported}
    else:
        status.pop("type_norm_unexported", None)
    profiles, unreadable = story_profiles(config["state_dir"])
    entity_failure = False
    if lookup_entities:
        try:
            entity_context.resolve(config["state_dir"], autopilot.entity_links(candidates, ledger), now=now)
        except Exception as error:
            # The cache is a convenience: whatever went wrong, publication goes on without it.
            LOG.warning("Entry link lookups stopped: %s", type(error).__name__)
            entity_failure = True
    inputs = autopilot.StoryInputs(profiles, entity_context.load(config["state_dir"]), {})
    staged = autopilot.stage(ledger, candidates, at=at, inputs=inputs)
    persist(ledger)
    client = connect()
    check_private = privacy_guard(config)
    snapshot, publication = autopilot.publish(
        ledger,
        client,
        persist=persist,
        at=at,
        validate_post=lambda post: check_private(json.dumps(post, ensure_ascii=False).encode()),
    )
    warnings = (
        ([warning[0]] if warning else [])
        + (["feed_ingest_error"] if status.get("feed_ingest_error") else [])
        + (["feed_synthetic"] if synthetic_held else [])
        + (["invalid_records"] if publication.get("invalid") else [])
        + (["type_profiles_unreadable"] if unreadable else [])
        + (["type_norm_unexported"] if unexported else [])
        + (["entity_lookup_failed"] if entity_failure else [])
        # Each story that could not be written is listed in the staged result.
        + (["editorial_failures"] if staged.get("editorial_failures") else [])
    )
    return snapshot, staged, publication, warnings


# The settings that reach the built pages; a change to any of them is a new release.
SITE_SETTINGS = (
    "site_name",
    "site_tagline",
    "site_description",
    "author_name",
    "support_link",
    "contact_email",
)


def reference_digest(config):
    """The SHA-256 of each pinned file present in the configured reference directory, by name."""
    directory = config.get("reference_dir")
    if not directory:
        return {}
    digests = {}
    for filename, _ in reference_data.FILES.values():
        path = Path(directory) / filename
        if path.is_file():
            digests[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def release_fingerprint(snapshot, config, photos=None):
    renderer = source_digest(
        [
            Path(__file__),
            *(ROOT / "themes/wtf-adsb").rglob("*"),
            *(ROOT / "tools/ghost-theme").glob("*.mjs"),
            *(ROOT / "tools/ghost-theme").glob("*.py"),
            *(ROOT / "tools/ghost-theme").glob("*.css"),
            *(ROOT / "tools/ghost-theme/data").rglob("*"),
            *(ROOT / "tools/ghost-theme/fonts").rglob("*"),
            *(ROOT / "src/wtf_adsb_publisher/data").rglob("*"),
            *(ROOT / "src/wtf_adsb_publisher").glob("*.py"),
        ]
    )
    return public._digest(
        {
            "events": snapshot["events"],
            "airframe_photos": photos or {},
            "day": snapshot["as_of_utc"][:10],
            "renderer": renderer,
            "private_terms": private_terms.pattern(config),
            "site_origin": site_origin.get(config),
            "site": {key: config.get(key) for key in SITE_SETTINGS},
            "about": site_identity.about_sha256(config),
            "references": reference_digest(config),
            "preview_ssh_host": config["preview_ssh_host"],
            "production": config.get("production", False),
            "plugins": {role: plugins.identity(role, config) for role in ("renderer", "deployer")},
        }
    )


def run(config, *, client=None, deploy=True):
    """One publication pass. Without `deploy`, Ghost and the saved snapshot change; the site does not."""
    if not isinstance(config.get("production", False), bool):
        raise ValueError("production must be a boolean")
    integrations = plugins.preflight(
        config, ("reader", "policy", "publisher", *(("renderer", "deployer") if deploy else ()))
    )
    approved = plugins.approved_extensions(config)
    ingest_error = None
    if config.get("feed_directory"):
        from wtf_adsb_publisher.ingest import ingest_directory

        try:
            ingest_directory(
                config["feed_directory"],
                config["inbox_database"],
                approved_extensions=approved,
                readback=False,
            )
        except (ValueError, OSError, sqlite3.Error) as error:
            # The inbox retains verified batches and explicit retractions. A later
            # failed batch holds new releases without suppressing those withdrawals.
            ingest_error = type(error).__name__
    state_dir = Path(config["state_dir"]).resolve()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)
    ledger_path, status_path = state_dir / "ledger.json", state_dir / "status.json"
    with public.ledger_lock(ledger_path):
        # Before anything is staged into it: an older ledger belongs to older code.
        ledger = autopilot.require_story_scheme(public.read_json(ledger_path))
        status = public.read_json(status_path) if status_path.exists() else {}
        if ingest_error:
            status["feed_ingest_error"] = ingest_error
        else:
            status.pop("feed_ingest_error", None)
        at = public.now_utc()
        previous_snapshot = state_dir / "snapshot.json"
        if previous_snapshot.exists():
            autopilot.remember_published(ledger, public.read_json(previous_snapshot))

        def persist(value):
            public.write_json_atomic(ledger_path, value, private=True)

        snapshot, staged, publication, warnings = publication_pass(
            config,
            ledger,
            status,
            lambda: client or integrations["publisher"].connect(config),
            persist,
            at,
            integrations=integrations,
        )
        # A pass that may deploy refreshes the photo cache; others only read it.
        photos, photo_status = airframe_photos(snapshot, config, fetch=deploy)
        fingerprint = release_fingerprint(snapshot, config, photos)
        if deploy and fingerprint != status.get("release_fingerprint"):
            build_release(snapshot, ledger, config, status, photos=photos, integrations=integrations)
            status["release_fingerprint"] = fingerprint
        elif not deploy:
            # Later passes and the weekly runner read the published export; the
            # last release and its fingerprint stay as they were.
            public.write_json_atomic(previous_snapshot, snapshot, private=True)
        status.update(
            last_run=public.now_utc(),
            staged=staged,
            publication=publication,
            events=len(snapshot["events"]),
            snapshot_id=snapshot["snapshot_id"],
            release_pending=fingerprint != status.get("release_fingerprint"),
            airframe_photos=photo_status,
        )
        if not publication["failed"]:
            status["last_success"] = status["last_run"]
        public.write_json_atomic(status_path, status, private=True)
        return {
            "events": len(snapshot["events"]),
            **staged,
            **publication,
            "deployed": deploy and status.get("release_fingerprint") == fingerprint,
            "warnings": warnings,
        }


def inventory_slugs(inventory):
    """Slugs of every post and page in a saved Ghost inventory, drafts included."""
    posts, pages = inventory.get("posts"), inventory.get("pages")
    if not isinstance(posts, list) or not isinstance(pages, list):
        raise ValueError("The Ghost inventory needs post and page lists, drafts included")
    slugs = set()
    for item in [*posts, *pages]:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str) or not item["slug"]:
            raise ValueError("Every Ghost post and page needs a slug")
        slugs.add(item["slug"])
    return slugs


def ghost_tag_slug(name):
    """The slug Ghost generates for a new tag; an internal #tag gets the hash- prefix."""
    slug = story_urls.ghost_slugify(name) or "tag"
    return "hash-" + slug if name.startswith("#") else slug


class SimulatedGhost:
    """Ghost as of a saved inventory. Writes change only this copy and are recorded.

    Like Ghost, an update needs the current `updated_at` token, and a slug used
    by another post or page gets a numeric suffix. Tags come back in the order
    sent, each with its name and slug: an existing tag is found by slug, or by
    name when none is sent, and a new one gets the slug Ghost would generate.
    Refusals carry the HTTP status Ghost would answer with.
    """

    def __init__(self, inventory, *, at):
        # Ghost keeps slugs unique across posts and pages, so both lists are required.
        inventory_slugs(inventory)
        self.posts = {}
        self.tags = {}
        for post in inventory["posts"]:
            if not post.get("id"):
                raise ValueError("Every saved Ghost post needs an ID and slug")
            if post["id"] in self.posts:
                raise ValueError("Duplicate saved Ghost post")
            self.posts[post["id"]] = deepcopy(post)
            for tag in post.get("tags") or ():
                if isinstance(tag, dict) and isinstance(tag.get("name"), str):
                    tag = {"name": tag["name"], "slug": tag.get("slug") or ghost_tag_slug(tag["name"])}
                    self.tags.setdefault(tag["slug"], tag)
        self.pages = {page["slug"] for page in inventory["pages"]}
        self.at = public.utc(at)
        self.writes = []

    def tag(self, sent):
        """The stored tag a sent tag resolves to, created on first use."""
        name, slug = sent.get("name"), sent.get("slug")
        if not isinstance(name, str) or not name:
            raise GhostError("Simulated Ghost validation failed (HTTP 422)", 422)
        if slug is None:
            slug = next(
                (key for key, tag in self.tags.items() if tag["name"].casefold() == name.casefold()),
                ghost_tag_slug(name),
            )
        return dict(self.tags.setdefault(slug, {"name": name, "slug": slug}))

    def inventory(self):
        return [deepcopy(post) for post in self.posts.values()]

    def slugs_in_use(self):
        """Post and page slugs in every status."""
        return {post["slug"] for post in self.posts.values()} | self.pages

    def save(self, post, current=None):
        if current is not None:
            stored = self.posts.get(current.get("id"))
            if stored is None:
                raise GhostError("Simulated Ghost request failed (HTTP 404)", 404)
            if stored.get("updated_at") != current.get("updated_at"):
                raise GhostError("Simulated Ghost update conflict (HTTP 409)", 409)
            saved = {**deepcopy(stored), **deepcopy(post)}
            action = (
                "suspend"
                if post.get("status") == "draft" and stored.get("status") == "published"
                else "update"
            )
        else:
            saved = {"visibility": "public", **deepcopy(post), "created_at": self.at}
            saved["id"] = hashlib.sha256(f"{post.get('slug')}\0{len(self.writes)}".encode()).hexdigest()[:24]
            action = "create"
        requested = saved.get("slug")
        taken = {item["slug"] for key, item in self.posts.items() if key != saved["id"]} | self.pages
        if requested in taken:
            suffix = 2
            while f"{requested}-{suffix}" in taken:
                suffix += 1
            saved["slug"] = f"{requested}-{suffix}"
        if "lexical" in post:
            saved["html"] = "\n".join(
                child["html"]
                for child in json.loads(post["lexical"])["root"]["children"]
                if child.get("type") == "html"
            )
        tags = []
        for tag in saved.get("tags") or ():
            resolved = self.tag(tag)
            # A tag sent twice is stored once, where it first appears.
            if not any(item["slug"] == resolved["slug"] for item in tags):
                tags.append(resolved)
        saved["tags"] = tags
        if saved.get("status") == "published" and not saved.get("published_at"):
            saved["published_at"] = self.at
        saved["updated_at"] = self.at
        self.posts[saved["id"]] = saved
        self.writes.append({"action": action, "requested_slug": requested, "slug": saved["slug"]})
        return deepcopy(saved)


class CachedBasemaps:
    """The shared map cache without downloads: an uncached background renders without a map.

    With `webp`, a cached map is embedded as WebP when that is smaller, exactly
    as a real pass does; its encoding is recorded in the cache beside the PNG.
    """

    def __init__(self, directory, *, webp=False):
        import basemap

        self.module = basemap
        self.cache = basemap.BasemapCache(directory, fetch=False, webp=webp)
        self.missing = 0

    @property
    def used(self):
        return self.cache.used

    @property
    def webp(self):
        return self.cache.webp

    def cached(self, bounds):
        spec = self.module.request_spec(bounds)
        if spec is None:
            return False
        if self.cache.cached_key(spec) is None:
            self.missing += 1
            return False
        return True

    def get(self, bounds):
        return self.cache.get(bounds) if self.cached(bounds) else None

    def image(self, bounds):
        return self.cache.image(bounds) if self.cached(bounds) else None


def slug_conflicts(ledger, writes):
    """Records a slug conflict holds back, with the slug Ghost assigned when it renamed one.

    The publisher checks every slug before it posts, so most conflicts stop a
    record without any write; a rename only happens in a race with another post.
    """
    renamed = {
        write["requested_slug"]: write["slug"] for write in writes if write["slug"] != write["requested_slug"]
    }
    conflicts = []
    for record in ledger["records"]:
        slug = (record.get("automation") or {}).get("slug_conflict")
        if slug:
            entry = {"record_id": record["id"], "slug": slug}
            if slug in renamed:
                entry["assigned_slug"] = renamed.pop(slug)
            conflicts.append(entry)
    return conflicts + [{"slug": slug, "assigned_slug": assigned} for slug, assigned in renamed.items()]


def dry_run(config, inventory, output, *, at=None, fetch_basemaps=False):
    """Simulate one pass from a saved Ghost inventory; no state outside `output` changes.

    The ledger, status and snapshot are read and never written. Ghost writes go
    to a simulated copy of the inventory. The would-be production site is built
    into `output` with the release privacy guard, and nothing is deployed. As in
    a real pass, the shared build caches can gain social cards and WebP copies
    of cached maps. Only `fetch_basemaps` may download public map backgrounds
    into the shared cache.
    """
    if not isinstance(config.get("production", False), bool):
        raise ValueError("production must be a boolean")
    integrations = plugins.preflight(config, ("reader", "policy", "renderer"))
    output = Path(output).resolve()
    artifacts = (ROOT / "artifacts/ghost-theme").resolve()
    work = artifacts / "autopilot"
    if (
        not output.is_relative_to(artifacts)
        or output == artifacts
        or output.is_relative_to(work)
        or work.is_relative_to(output)
    ):
        raise ValueError("Dry-run output must be its own directory under artifacts/ghost-theme")
    state_dir = Path(config["state_dir"]).resolve()
    ledger = autopilot.require_story_scheme(public.read_json(state_dir / "ledger.json"))
    status = public.read_json(state_dir / "status.json") if (state_dir / "status.json").exists() else {}
    at = public.stamp(at)
    client = SimulatedGhost(public.read_json(inventory), at=at)
    previous_snapshot = state_dir / "snapshot.json"
    if previous_snapshot.exists():
        autopilot.remember_published(ledger, public.read_json(previous_snapshot))
    snapshot, staged, publication, warnings = publication_pass(
        config,
        ledger,
        status,
        lambda: client,
        lambda value: None,
        at,
        simulate=True,
        integrations=integrations,
        lookup_entities=False,
    )
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    snapshot_path = output / "snapshot.json"
    public.write_json_atomic(snapshot_path, snapshot, private=True)
    cache = ROOT / "artifacts/ghost-theme/basemap-cache"
    basemaps = release_basemaps(cache) if fetch_basemaps else CachedBasemaps(cache, webp=True)
    previous = output / "media/manifest.json"
    # A dry run reads the photo cache and never sends a request.
    photos, _ = airframe_photos(snapshot, config, fetch=False)
    site, files = build_site(
        snapshot_path,
        snapshot,
        ledger,
        config,
        output,
        previous=previous if previous.exists() else None,
        basemaps=basemaps,
        photos=photos,
        integrations=integrations,
    )
    events = snapshot["events"]
    totals = Counter(event["observed_start"][:10] for event in events)
    # The publisher's own decision: priority evidence alone does not make a story exceptional.
    granted = {
        record["id"]
        for record in ledger["records"]
        if (record.get("automation") or {}).get("published_exceptional") is True
    }
    ordinary = Counter(event["observed_start"][:10] for event in events if event["id"] not in granted)
    actions = Counter(write["action"] for write in client.writes)
    report = {
        "dry_run": True,
        "as_of_utc": at,
        "would_create": actions["create"],
        "would_update": actions["update"],
        "would_suspend": actions["suspend"],
        **staged,
        **publication,
        "events": len(events),
        "snapshot_id": snapshot["snapshot_id"],
        "max_ordinary_per_day": max(ordinary.values(), default=0),
        "per_day": {
            day: {"total": total, "ordinary": ordinary[day]} for day, total in sorted(totals.items())
        },
        "weekly_picks": [
            {
                "week_start": event["weekly_spotlight"]["week_start"],
                "id": event["id"],
                "article_path": event["article_path"],
            }
            for event in sorted(events, key=lambda item: item["observed_start"])
            if "weekly_spotlight" in event
        ],
        "slug_conflicts": slug_conflicts(ledger, client.writes),
        "would_deploy": release_fingerprint(snapshot, config, photos) != status.get("release_fingerprint"),
        "airframe_photos": len(photos),
        "basemaps_missing": getattr(basemaps, "missing", 0),
        "build": "ok",
        "privacy": "ok",
        "files": len(files),
        "site": str(site),
        "warnings": warnings,
    }
    public.write_json_atomic(output / "report.json", report, private=True)
    public.write_json_atomic(
        output / "decisions.json",
        {
            "ledger": ledger,
            "writes": client.writes,
        },
        private=True,
    )
    return report


def reproject(config, *, apply=False, at=None):
    """Re-derive saved packets after a change in what evidence_packet() derives.

    See autopilot.reproject. Without `apply` nothing is written: the report shows
    what a run with `apply` would change. With it, the ledger is written once,
    under its lock, at the end. Pause the publisher first and run a --no-deploy
    pass afterwards.
    """
    state_dir = Path(config["state_dir"]).resolve()
    ledger_path = state_dir / "ledger.json"
    with public.ledger_lock(ledger_path) if apply else nullcontext():
        ledger = autopilot.require_story_scheme(public.read_json(ledger_path))
        profiles, unreadable = story_profiles(state_dir)
        inputs = autopilot.StoryInputs(profiles, entity_context.load(state_dir), {})
        report = autopilot.reproject(ledger, public.stamp(at), inputs=inputs)
        if apply and report["reprojected"]:
            public.write_json_atomic(ledger_path, ledger, private=True)
    return {**report, "applied": apply, "warnings": ["type_profiles_unreadable"] if unreadable else []}


def refresh_entities(config):
    """Mark the cached Wikidata answers stale so the next passes look each entry link up again."""
    state_dir = Path(config["state_dir"]).resolve()
    with public.ledger_lock(state_dir / "ledger.json"):
        return {"marked_stale": entity_context.refresh(state_dir)}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--no-deploy", action="store_true", help="Publish to Ghost but send no release")
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate a pass; saves no state and sends nothing"
    )
    parser.add_argument("--inventory", type=Path, help="Saved Ghost inventory for --dry-run")
    parser.add_argument("--output", type=Path, help="Dry-run directory under artifacts/ghost-theme")
    parser.add_argument(
        "--fetch-basemaps", action="store_true", help="Let --dry-run download missing public map backgrounds"
    )
    parser.add_argument(
        "--reproject",
        action="store_true",
        help="Re-derive saved packets after a change in the evidence code; report what would change",
    )
    parser.add_argument("--apply", action="store_true", help="With --reproject, write the ledger")
    parser.add_argument(
        "--refresh-entities",
        action="store_true",
        help="Mark the cached Wikidata answers for watchlist entry links stale; later passes look them up again",
    )
    args = parser.parse_args()
    if args.refresh_entities and (
        args.reproject or args.apply or args.dry_run or args.no_deploy or args.inventory or args.output
    ):
        parser.error("--refresh-entities takes only --config")
    if args.reproject and (args.dry_run or args.no_deploy or args.inventory or args.output):
        parser.error("--reproject takes only --config and --apply")
    if args.apply and not args.reproject:
        parser.error("--apply is for --reproject")
    if args.dry_run and (args.inventory is None or args.output is None):
        parser.error("--dry-run needs --inventory and --output")
    if not args.dry_run and (args.inventory or args.output or args.fetch_basemaps):
        parser.error("--inventory, --output and --fetch-basemaps are dry-run options")
    if args.dry_run and args.no_deploy:
        parser.error("--dry-run never deploys; omit --no-deploy")
    os.umask(0o077)
    started = time.monotonic()
    try:
        config = public.load_config(args.config)
        if args.refresh_entities:
            print(json.dumps(refresh_entities(config)))
            return
        if args.reproject:
            result = reproject(config, apply=args.apply)
            print(json.dumps(result))
            if result["unresolved"]:
                raise SystemExit(1)
            return
        if args.dry_run:
            result = dry_run(config, args.inventory, args.output, fetch_basemaps=args.fetch_basemaps)
        else:
            result = run(config, deploy=not args.no_deploy)
        print(json.dumps({**result, "elapsed_seconds": round(time.monotonic() - started, 1)}))
        if result["failed"]:
            raise SystemExit(1)
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        if args.reproject:
            # The ledger is written only after every record was handled.
            detail = f": {error}" if isinstance(error, ValueError) else ""
            parser.exit(
                1, f"Reprojection failed ({type(error).__name__}{detail}); the ledger is unchanged.\n"
            )
        if args.dry_run:
            # These messages name a check, never the private text or a key.
            detail = f": {error}" if isinstance(error, (ValueError, RuntimeError)) else ""
            parser.exit(
                1,
                f"Dry run failed ({type(error).__name__}{detail}); the ledger, Ghost and the site are unchanged.\n",
            )
        parser.exit(
            1,
            f"Automatic publication failed ({type(error).__name__}); the last successful site remains available.\n",
        )


if __name__ == "__main__":
    main()
