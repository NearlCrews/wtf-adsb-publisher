#!/usr/bin/env python3
"""Set the Ghost posts a ledger owns to draft, and restore them. Nothing is deleted.

A post is owned only when its ID, slug and #autopilot-<record UUID> tag all match
a record. --plan reads Ghost and writes the list of owned published posts.
--apply drafts exactly that list with each post's update token, recording every
post in the receipt before and after its request, so a rerun resumes. A request
whose reply was lost is settled from the next inventory: the change counts as
this tool's only when Ghost's update time falls within the request's window and
the post's content is still what the request left alone, and a retry needs the
token the request was sent with. A request Ghost refused (HTTP 4xx) changed
nothing and is retried. --restore republishes exactly the posts the receipt
shows this tool drafted, marks the retirement rolled back so it cannot resume,
and leaves edited drafts alone. Posts the ledger does not own are listed, never
changed.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher.autopilot import owner_tag
from wtf_adsb_publisher.ghost_client import GHOST_ERRORS, GhostClient, GhostError

SCHEMA_VERSION = 1
WRITE_ERRORS = (*GHOST_ERRORS, TypeError)
# Results a rerun reconsiders; every other result is final.
OPEN = {"pending", "uncertain", "refused"}
# Ghost's update time for a write it committed: after the request started (less
# clock skew) and before the client's 30-second timeout ended (plus skew).
SKEW_SECONDS = 60
TIMEOUT_SECONDS = 30
# What a status write leaves as it was: the post's content and settings. Ghost
# changes the status, update time, publication time and URL itself.
CONTENT_FIELDS = (
    "title",
    "slug",
    "html",
    "lexical",
    "mobiledoc",
    "custom_excerpt",
    "feature_image",
    "feature_image_alt",
    "feature_image_caption",
    "featured",
    "visibility",
    "custom_template",
    "canonical_url",
    "codeinjection_head",
    "codeinjection_foot",
    "meta_title",
    "meta_description",
    "og_title",
    "og_description",
    "og_image",
    "twitter_title",
    "twitter_description",
    "twitter_image",
)


def sealed(value):
    return {**value, "sha256": public._digest(value)}


def unsealed(value, kind):
    body = {key: item for key, item in value.items() if key != "sha256"}
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("kind") != kind
        or value.get("sha256") != public._digest(body)
    ):
        raise ValueError(f"Not an unmodified {kind} file")
    return body


def owners(ledger):
    """Each record's expected post: (Ghost ID, slug, tag) by record ID. The ledger is read raw.

    The slug is the article path's last segment, which is the Ghost slug in the
    retired ledger's root-level URLs as in /stories/ ones (public.article_slug
    reads only the latter).
    """
    expected = {}
    for record in ledger.get("records", []):
        editorial = record.get("editorial") or {}
        path = editorial.get("article_path")
        if not isinstance(path, str) or not isinstance(record.get("id"), str):
            continue
        ghost_id = editorial.get("ghost_id") or next(
            (
                revision["editorial"]["ghost_id"]
                for revision in reversed(record.get("history", []))
                if isinstance(revision.get("editorial"), dict) and revision["editorial"].get("ghost_id")
            ),
            None,
        )
        expected[record["id"]] = (ghost_id, path.strip("/").split("/")[-1], owner_tag(record["id"]))
    return expected


def tags(post):
    return {tag.get("name") for tag in post.get("tags") or [] if isinstance(tag, dict)}


def owned(post, ghost_id, slug, tag):
    return post.get("id") == ghost_id and post.get("slug") == slug and tag in tags(post)


def content_digest(post):
    """The post's content and settings (CONTENT_FIELDS) with its tag names, as a digest."""
    names = sorted(name for name in tags(post) if isinstance(name, str))
    return public._digest({**{key: post.get(key) for key in CONTENT_FIELDS}, "tags": names})


def _incomplete(record_id, post, reason):
    """A post that only partly matches a record: listed for review, never changed."""
    return {
        "record_id": record_id,
        "post_id": post["id"],
        "slug": post.get("slug"),
        "status": post.get("status"),
        "reason": reason,
    }


def plan(client, ledger_path, *, at=None):
    """Read-only: the owned published posts to draft and everything else left alone."""
    ledger_path = Path(ledger_path)
    expected = owners(public.read_json(ledger_path))
    posts = client.inventory()
    by_id = {post["id"]: post for post in posts}
    result = {"to_draft": [], "owned_drafts": [], "owned_other": [], "incomplete_ownership": []}
    claimed = set()
    for record_id, (ghost_id, slug, tag) in sorted(expected.items()):
        post = by_id.get(ghost_id) if ghost_id else None
        if post is not None and post["id"] in claimed:
            # A malformed ledger can pin one post twice; it is retired once.
            result["incomplete_ownership"].append(_incomplete(record_id, post, "claimed_twice"))
            continue
        if post is None or not owned(post, ghost_id, slug, tag):
            # Partial matches are not proof of ownership; they are listed for review.
            if post is not None:
                result["incomplete_ownership"].append(_incomplete(record_id, post, "slug_or_tag_changed"))
            result["incomplete_ownership"].extend(
                _incomplete(record_id, other, "not_the_pinned_post")
                for other in posts
                if other is not post and other.get("slug") == slug and tag in tags(other)
            )
            continue
        claimed.add(post["id"])
        entry = {
            "record_id": record_id,
            "post_id": post["id"],
            "slug": slug,
            "status": post.get("status"),
            "updated_at": post.get("updated_at"),
            "published_at": post.get("published_at"),
        }
        status = post.get("status")
        result[
            "to_draft" if status == "published" else "owned_drafts" if status == "draft" else "owned_other"
        ].append(entry)
    unowned = [
        {"post_id": post["id"], "slug": post.get("slug"), "status": post.get("status")}
        for post in posts
        if post["id"] not in claimed
    ]
    missing = sorted(
        record_id
        for record_id, (ghost_id, slug, tag) in expected.items()
        if not ghost_id or ghost_id not in by_id
    )
    return sealed(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "retire-plan",
            "created_at": public.stamp(at),
            "ledger": str(ledger_path.resolve()),
            "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
            **result,
            "unowned": unowned,
            "records_without_post": missing,
            "summary": {
                **{name: len(items) for name, items in result.items()},
                "unowned": len(unowned),
                "records_without_post": len(missing),
            },
        }
    )


def _receipt(path, kind, source, sha256):
    if Path(path).exists():
        receipt = unsealed(public.read_json(path), kind)
        if receipt["source_sha256"] != sha256:
            raise ValueError("The receipt belongs to another plan or retirement")
        return receipt
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "source": str(Path(source).resolve()),
        "source_sha256": sha256,
        "posts": {},
    }


def _save_receipt(path, receipt, at):
    receipt["updated_at"] = public.stamp(at)
    results = Counter(entry["result"] for entry in receipt["posts"].values())
    # An unresolved write, a refusal or a post not reached yet leaves the run unfinished.
    receipt["summary"] = {**dict(sorted(results.items())), "failed": sum(results[name] for name in OPEN)}
    public.write_json_atomic(path, sealed(receipt), private=True)


def refused(error):
    """Ghost answered with HTTP 4xx, so the request changed nothing."""
    status = getattr(error, "status", None)
    return isinstance(status, int) and 400 <= status < 500


def ours(entry, post):
    """Whether the post's last change is the write this entry sent and lost the reply to.

    Ghost's update time must fall in the request's window, and the content the
    request left alone must be what it was sent with: an owner's edit inside the
    window is not this tool's write. A request recorded without its content
    cannot be told apart from an edit, so it is not claimed.
    """
    try:
        updated = public._instant(post.get("updated_at")).timestamp()
        attempted = public._instant(entry["attempted_at"]).timestamp()
        expected = entry["sent_content"]
    except (ValueError, TypeError, KeyError):
        return False
    return (
        attempted - SKEW_SECONDS <= updated <= attempted + TIMEOUT_SECONDS + SKEW_SECONDS
        and content_digest(post) == expected
    )


def _change(client, receipt, path, entry, current, change, success, at):
    """One status write, recorded first so a lost reply is recognized on the next run.

    Returns False only when the outcome is unknown; the pass then stops.
    """
    entry.update(
        result="uncertain",
        attempted_at=public.stamp(at),
        sent_token=current.get("updated_at"),
        sent_content=content_digest(current),
    )
    _save_receipt(path, receipt, at)
    try:
        saved = client.save(change, current)
        if saved.get("id") != entry["post_id"] or saved.get("status") != change["status"]:
            raise ValueError("Ghost did not confirm the status change")
    except WRITE_ERRORS as error:
        if refused(error):
            entry["result"] = "refused"
        _save_receipt(path, receipt, at)
        return entry["result"] == "refused"
    entry.update(result=success, after={"status": saved["status"], "updated_at": saved.get("updated_at")})
    _save_receipt(path, receipt, at)
    return saved


def apply(client, plan_path, receipt_path, *, at=None):
    """Draft exactly the planned posts; a rerun with the same receipt resumes."""
    source = public.read_json(plan_path)
    retirement = unsealed(source, "retire-plan")
    with public.ledger_lock(receipt_path):
        receipt = _receipt(receipt_path, "retire-receipt", plan_path, source["sha256"])
        if receipt.get("rolled_back_at"):
            raise ValueError("This retirement was restored; plan again to retire these posts")
        posts = {post["id"]: post for post in client.inventory()}
        for item in retirement["to_draft"]:
            receipt["posts"].setdefault(
                item["post_id"],
                {
                    "post_id": item["post_id"],
                    "slug": item["slug"],
                    "record_id": item["record_id"],
                    "before": {key: item[key] for key in ("status", "updated_at", "published_at")},
                    "result": "pending",
                },
            )
        for item in retirement["to_draft"]:
            entry = receipt["posts"][item["post_id"]]
            current = posts.get(item["post_id"])
            if entry["result"] == "drafted" and current is not None and current.get("status") != "draft":
                # Republished since; it is not drafted again.
                entry["result"] = "no_longer_draft"
            if entry["result"] not in OPEN:
                continue
            tag = owner_tag(item["record_id"])
            if current is None:
                entry["result"] = "missing"
            elif not owned(current, item["post_id"], item["slug"], tag):
                entry["result"] = "ownership_changed"
            elif current.get("status") == "draft":
                # After a lost reply the draft is ours only if Ghost made it during our
                # request and nobody edited it meanwhile.
                mine = entry["result"] == "uncertain" and ours(entry, current)
                entry["result"] = "drafted" if mine else "already_draft"
                entry["after"] = {"status": "draft", "updated_at": current.get("updated_at")}
            elif current.get("status") != "published":
                entry["result"] = "status_changed"
            elif current.get("updated_at") != item["updated_at"]:
                entry["result"] = "changed_since_plan"
            else:
                saved = _change(
                    client, receipt, receipt_path, entry, current, {"status": "draft"}, "drafted", at
                )
                if saved is False:
                    break
                if isinstance(saved, dict):
                    posts[saved["id"]] = saved
            _save_receipt(receipt_path, receipt, at)
        _save_receipt(receipt_path, receipt, at)
    return receipt["summary"]


def restore(client, receipt_path, output_path, *, at=None):
    """Republish exactly the posts this tool drafted, keeping their dates.

    The retirement is marked rolled back first, so it cannot resume afterwards.
    A draft whose confirmation was lost counts only when Ghost made it during the
    request and its content is unchanged; a post edited while retired stays a draft.
    """
    receipt_path, output_path = Path(receipt_path), Path(output_path)
    try:
        same_file = receipt_path.samefile(output_path)
    except FileNotFoundError:
        same_file = False
    if receipt_path.resolve() == output_path.resolve() or same_file:
        raise ValueError("Retirement and restore receipts must use different files")
    with public.ledger_lock(receipt_path):
        source = public.read_json(receipt_path)
        retirement = unsealed(source, "retire-receipt")
        if not retirement.get("rolled_back_at"):
            retirement["rolled_back_at"] = public.stamp(at)
            public.write_json_atomic(receipt_path, sealed(retirement), private=True)
        with public.ledger_lock(output_path):
            # Bound to the reviewed plan, which stays fixed while the receipt is rewritten.
            receipt = _receipt(output_path, "restore-receipt", receipt_path, retirement["source_sha256"])
            posts = {post["id"]: post for post in client.inventory()}
            drafted = [
                item for item in retirement["posts"].values() if item["result"] in {"drafted", "uncertain"}
            ]
            for item in drafted:
                receipt["posts"].setdefault(
                    item["post_id"],
                    {
                        "post_id": item["post_id"],
                        "slug": item["slug"],
                        "record_id": item["record_id"],
                        "retired_as": item["result"],
                        "before": item.get("after"),
                        "result": "pending",
                    },
                )
            for item in drafted:
                entry = receipt["posts"][item["post_id"]]
                if entry["result"] not in OPEN:
                    continue
                current = posts.get(item["post_id"])
                if current is None:
                    entry["result"] = "missing"
                elif not owned(current, item["post_id"], item["slug"], owner_tag(item["record_id"])):
                    entry["result"] = "ownership_changed"
                elif entry["result"] == "uncertain":
                    # Our own publish request lost its reply: settle it, or retry with its token.
                    if current.get("status") == "published":
                        entry["result"] = "republished" if ours(entry, current) else "already_published"
                    elif current.get("status") != "draft" or current.get("updated_at") != entry.get(
                        "sent_token"
                    ):
                        entry["result"] = "changed"
                elif current.get("status") == "published":
                    entry["result"] = "not_drafted" if item["result"] == "uncertain" else "already_published"
                elif current.get("status") != "draft":
                    entry["result"] = "changed"
                elif item["result"] == "drafted":
                    # Edited or rescheduled while retired: publishing now would release unseen changes.
                    if current.get("updated_at") != (item.get("after") or {}).get("updated_at"):
                        entry["result"] = "changed"
                elif not ours(item, current):
                    # The draft request never confirmed, and this draft is not from it.
                    entry["result"] = "changed"
                if entry["result"] in OPEN:
                    change = {"status": "published"}
                    if item["before"].get("published_at"):
                        change["published_at"] = item["before"]["published_at"]
                    if (
                        _change(client, receipt, output_path, entry, current, change, "republished", at)
                        is False
                    ):
                        break
                _save_receipt(output_path, receipt, at)
            _save_receipt(output_path, receipt, at)
    return receipt["summary"]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, help="Ledger whose posts are retired (read only)")
    parser.add_argument("--plan", type=Path, help="Plan to write, or with --apply the reviewed plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--receipt", type=Path, help="Receipt for --apply or --restore (resumed when present)"
    )
    parser.add_argument(
        "--restore", type=Path, metavar="RECEIPT", help="Republish the posts this receipt drafted"
    )
    args = parser.parse_args()
    if args.restore and (args.ledger or args.plan or args.apply):
        parser.error("--restore takes only --config and an optional --receipt")
    if not args.restore and (args.ledger is None or args.plan is None):
        parser.error("Planning and applying need --ledger and --plan")
    if args.apply and args.receipt is None:
        parser.error("--apply needs --receipt")
    if args.receipt and not (args.apply or args.restore):
        parser.error("--receipt is for --apply or --restore")
    os.umask(0o077)
    try:
        config = public.load_config(args.config)
        client = GhostClient(config["ghost_origin"], Path(config["ghost_key_file"]).read_text())
        if args.restore:
            output = args.receipt or args.restore.with_name(args.restore.stem + ".restore.json")
            summary = restore(client, args.restore, output)
        elif args.apply:
            retirement = unsealed(public.read_json(args.plan), "retire-plan")
            if retirement["ledger"] != str(args.ledger.resolve()):
                parser.error("The plan was made for another ledger")
            summary = apply(client, args.plan, args.receipt)
        else:
            if args.plan.exists():
                parser.error("Choose a new plan file; a reviewed plan is never replaced")
            retirement = plan(client, args.ledger)
            public.write_json_atomic(args.plan, retirement, private=True)
            summary = retirement["summary"]
        print(json.dumps(summary))
        if summary.get("failed"):
            raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, GhostError) as error:
        # These messages are this tool's or Ghost's status line; neither carries the key.
        detail = f": {error}" if isinstance(error, (ValueError, GhostError)) else ""
        parser.exit(1, f"Post retirement stopped ({type(error).__name__}{detail}); no post was deleted.\n")


if __name__ == "__main__":
    main()
