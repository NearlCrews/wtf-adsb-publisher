"""Transactional consumer of the independently versioned evidence protocol.

This module uses only this project's wire contract and its own SQLite state.
An input checkpoint advances in the same transaction as the corresponding
observations. Missing observations never imply a withdrawal.
"""

import fcntl
import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from jsonschema import Draft202012Validator

from . import wire


@contextmanager
def connect(database):
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS feed_stream(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    stream_id TEXT NOT NULL,generation TEXT NOT NULL,cursor INTEGER NOT NULL,
                    synthetic INTEGER,health TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feed_observations(
                    event_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,digest TEXT,
                    observation TEXT,active INTEGER NOT NULL,reason_code TEXT,quarantined INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS feed_changes(sequence INTEGER PRIMARY KEY,digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feed_batches(path TEXT PRIMARY KEY,sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feed_schema_declarations(event_id TEXT PRIMARY KEY,declarations TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feed_replacements(event_id TEXT PRIMARY KEY,replacements TEXT NOT NULL);
            """)
        path.chmod(0o600)
        with db:
            yield db
    finally:
        db.close()


def _unknown_extensions(batch, observation, approved):
    declarations = {entry["namespace"]: entry for entry in batch.get("extension_schemas", [])}
    for extension in observation.get("extensions", []):
        declaration = declarations[extension["namespace"]]
        expected = approved.get((extension["namespace"], extension["schema_version"]))
        if not isinstance(expected, dict) or expected.get("sha256") != declaration["sha256"]:
            return True
        if not expected["validator"].is_valid(extension["payload"]):
            return True
    return False


def load_extension_schemas(entries):
    """Load explicitly reviewed, digest-pinned local consumer contracts."""
    result = {}
    for entry in entries:
        wire.keys(entry, ("namespace", "schema_version", "path", "sha256"))
        key = (entry["namespace"], entry["schema_version"])
        if key in result:
            raise ValueError("Duplicate approved extension schema")
        schema = wire.loads(Path(entry["path"]).read_bytes())
        if wire.digest(schema) != entry["sha256"]:
            raise ValueError("Approved extension schema digest changed")
        Draft202012Validator.check_schema(schema)

        def local(node):
            if isinstance(node, dict):
                if any(key in node for key in ("$dynamicRef", "$recursiveRef")):
                    raise ValueError("Dynamic schema resolution is unsupported")
                if "$ref" in node and not node["$ref"].startswith("#/"):
                    raise ValueError("Consumer extension schemas cannot load remote resources")
                for value in node.values():
                    local(value)
            elif isinstance(node, list):
                for value in node:
                    local(value)

        local(schema)
        result[key] = {"sha256": entry["sha256"], "validator": Draft202012Validator(schema)}
    return result


def _apply(db, batch, approved):
    for change in batch["changes"]:
        previous = db.execute(
            "SELECT * FROM feed_observations WHERE event_id=?", (change["event_id"],)
        ).fetchone()
        revision = change["revision"]
        if previous and revision != previous["revision"] + 1:
            raise ValueError("Observation revision is repeated, missing, or out of order")
        if not previous and revision != 1:
            raise ValueError("Initial observation revision must be one")
        if change["operation"] == "upsert":
            payload = change["observation"]
            quarantine = _unknown_extensions(batch, payload, approved)
            db.execute(
                "INSERT OR REPLACE INTO feed_observations VALUES (?,?,?,?,1,NULL,?)",
                (
                    change["event_id"],
                    revision,
                    change["evidence_digest"],
                    wire.canonical(payload).decode(),
                    quarantine,
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO feed_schema_declarations VALUES (?,?)",
                (change["event_id"], wire.canonical(batch.get("extension_schemas", [])).decode()),
            )
        else:
            if previous is None:
                raise ValueError("Retraction has no preceding observation")
            # Remove evidence from the consumer's active materialized view.
            db.execute(
                "UPDATE feed_observations SET revision=?,digest=NULL,observation=NULL,active=0,"
                "reason_code=?,quarantined=0 WHERE event_id=?",
                (revision, change["reason_code"], change["event_id"]),
            )
            db.execute("DELETE FROM feed_schema_declarations WHERE event_id=?", (change["event_id"],))
            db.execute(
                "INSERT OR REPLACE INTO feed_replacements VALUES (?,?)",
                (change["event_id"], wire.canonical(change.get("superseded_by", [])).decode()),
            )
        db.execute("INSERT INTO feed_changes VALUES (?,?)", (change["sequence"], wire.digest(change)))


def ingest_directory(
    export_root, publisher_database, *, allow_synthetic=False, approved_extensions=None, readback=True
):
    """Import durably; skip the returned state when a reader will load it separately."""
    path = Path(publisher_database)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(str(path) + ".ingest.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another importer owns this inbox") from error
        return _ingest_directory(
            export_root,
            publisher_database,
            allow_synthetic=allow_synthetic,
            approved_extensions=approved_extensions,
            readback=readback,
        )


def _ingest_directory(
    export_root, publisher_database, *, allow_synthetic=False, approved_extensions=None, readback=True
):
    """Import a complete immutable prefix; never execute content from the feed.

    Unknown extension schemas enter durable quarantine and hold new publication.
    Generation changes and retention gaps require an explicit recovery procedure;
    this release never silently resets a consumer or infers deletions.
    """
    approved = approved_extensions or {}
    try:
        manifest = wire.validate_manifest(wire.loads(wire.read_file(export_root, "manifest.json")))
        with connect(publisher_database) as db:
            current = db.execute("SELECT * FROM feed_stream WHERE singleton=1").fetchone()
            if current:
                if any(current[key] != manifest[key] for key in ("stream_id", "generation")):
                    raise ValueError("Stream identity changed; explicit consumer recovery is required")
                if manifest["head_sequence"] < current["cursor"]:
                    raise ValueError("Export manifest rolled back")
                if wire.instant(manifest["updated_at"]) < wire.instant(current["updated_at"]):
                    raise ValueError("Export health timestamp rolled back")
                cursor = current["cursor"]
                if current["synthetic"] and not allow_synthetic:
                    raise ValueError("Synthetic inbox is not enabled")
            else:
                cursor = 0
                health = {**manifest["health"], "ready": False, "complete": False}
                db.execute(
                    "INSERT INTO feed_stream VALUES (1,?,?,0,NULL,?,?)",
                    (
                        manifest["stream_id"],
                        manifest["generation"],
                        wire.canonical(health).decode(),
                        manifest["updated_at"],
                    ),
                )
            if manifest["first_sequence"] != 1:
                raise ValueError(
                    "A complete replay history is required; automatic retention recovery is unsupported"
                )
            # Publish a durable incomplete status before committing any new
            # prefix. Other processes must not publish from an intermediate view.
            pending = {**manifest["health"], "ready": False, "complete": False}
            db.execute(
                "UPDATE feed_stream SET health=? WHERE singleton=1", (wire.canonical(pending).decode(),)
            )
            db.commit()
            for entry in manifest["batches"]:
                known = db.execute(
                    "SELECT sha256 FROM feed_batches WHERE path=?", (entry["path"],)
                ).fetchone()
                if entry["sequence_end"] <= cursor:
                    if known is None or known[0] != entry["sha256"]:
                        raise ValueError("Previously imported batch changed")
                    continue
                if entry["sequence_start"] != cursor + 1:
                    raise ValueError("Consumer sequence gap")
                artifact = wire.read_file(export_root, entry["path"])
                if len(artifact) != entry["bytes"] or hashlib.sha256(artifact).hexdigest() != entry["sha256"]:
                    raise ValueError("Batch integrity check failed")
                batch = wire.validate_batch(wire.loads(artifact), allow_synthetic=allow_synthetic)
                if any(batch[key] != manifest[key] for key in ("stream_id", "generation")):
                    raise ValueError("Batch stream identity mismatch")
                if any(batch[key] != entry[key] for key in ("sequence_start", "sequence_end")):
                    raise ValueError("Batch inventory interval mismatch")
                bound = db.execute("SELECT synthetic FROM feed_stream WHERE singleton=1").fetchone()[0]
                if bound is not None and bool(bound) != batch["synthetic"]:
                    raise ValueError("Cannot mix synthetic and real evidence")
                _apply(db, batch, approved)
                cursor = batch["sequence_end"]
                db.execute("INSERT INTO feed_batches VALUES (?,?)", (entry["path"], entry["sha256"]))
                db.execute(
                    "UPDATE feed_stream SET cursor=?,synthetic=? WHERE singleton=1",
                    (cursor, batch["synthetic"]),
                )
                # Each durable batch and cursor are one transaction. A later bad
                # batch must not erase successful work or skip the failed batch.
                db.commit()
            for record in db.execute(
                "SELECT o.event_id,o.observation,o.quarantined,s.declarations FROM feed_observations o "
                "JOIN feed_schema_declarations s USING(event_id) "
                "WHERE o.active=1 AND (s.declarations!='[]' OR o.quarantined!=0)"
            ):
                observation = json.loads(record["observation"])
                declarations = json.loads(record["declarations"])
                quarantined = _unknown_extensions({"extension_schemas": declarations}, observation, approved)
                if quarantined != record["quarantined"]:
                    db.execute(
                        "UPDATE feed_observations SET quarantined=? WHERE event_id=?",
                        (quarantined, record["event_id"]),
                    )
            db.execute(
                "UPDATE feed_stream SET health=?,updated_at=? WHERE singleton=1",
                (wire.canonical(manifest["health"]).decode(), manifest["updated_at"]),
            )
        return read_state(publisher_database) if readback else None
    except Exception:
        # Preserve previously imported evidence while preventing stale readiness
        # from surviving a failed integrity check.
        with connect(publisher_database) as db:
            row = db.execute("SELECT health FROM feed_stream WHERE singleton=1").fetchone()
            if row:
                health = json.loads(row[0])
                health.update(ready=False, complete=False)
                db.execute(
                    "UPDATE feed_stream SET health=? WHERE singleton=1", (wire.canonical(health).decode(),)
                )
        raise


def read_state(database):
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        row = db.execute("SELECT * FROM feed_stream WHERE singleton=1").fetchone()
        if row is None:
            return {
                "observations": [],
                "retractions": [],
                "health": {
                    "ready": False,
                    "complete": False,
                    "mode": "live",
                    "last_receiver_poll": None,
                    "analysis_watermark": None,
                },
                "stream_id": None,
                "generation": None,
                "cursor": 0,
                "synthetic": False,
                "quarantined": 0,
            }
        observations, retractions, quarantine = [], [], 0
        for record in db.execute(
            "SELECT o.*,r.replacements FROM feed_observations o "
            "LEFT JOIN feed_replacements r USING(event_id) ORDER BY o.event_id"
        ):
            if record["quarantined"]:
                quarantine += 1
            elif record["active"]:
                observations.append(
                    {
                        "event_id": record["event_id"],
                        "revision": record["revision"],
                        "observation": json.loads(record["observation"]),
                    }
                )
            else:
                retractions.append(
                    {
                        "event_id": record["event_id"],
                        "revision": record["revision"],
                        "reason_code": record["reason_code"],
                        "superseded_by": json.loads(record["replacements"]) if record["replacements"] else [],
                    }
                )
        health = json.loads(row["health"])
        if quarantine:
            health.update(ready=False, complete=False)
        return {
            "observations": observations,
            "retractions": retractions,
            "health": health,
            "stream_id": row["stream_id"],
            "generation": row["generation"],
            "cursor": row["cursor"],
            "synthetic": bool(row["synthetic"]),
            "quarantined": quarantine,
        }
