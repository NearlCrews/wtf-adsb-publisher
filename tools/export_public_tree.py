#!/usr/bin/env python3
"""Export one commit's tracked files as the first commit of a new public repository.

The private history carries site-specific names in every commit, so the public repository
starts from a single commit made from a cleaned copy of one tree. The tool reads each file
from the Git object store, never from the working tree, so untracked or modified files
cannot leak. It leaves out private and generated paths, scans every written file and path
name for the strings listed in the forbidden file, and removes everything it wrote when
any of them matches. Reports name the path, the line number, and the line of the forbidden
file that matched; they never print the matched text.

    export_public_tree.py --source REPO --commit REF --destination DIR --forbidden FILE \
        [--message TEXT] [--author "Name <email>"] [--trailer LINE ...]

On success it prints a JSON object with the tree hash, the commit id, the file count, and
the excluded path count. --author sets both the author and the committer. Without it, the
inherited GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL, GIT_COMMITTER_NAME, and GIT_COMMITTER_EMAIL
must all be set; the tool refuses rather than fall back to Git's configured identity. Every
git call ignores the other inherited GIT_* variables and runs no hooks, and the new
repository starts without a template.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

EXCLUDED_ROOTS = frozenset(
    {
        ".private",
        ".remember",
        ".claude",
        ".superpowers",
        ".sync",
        "artifacts",
        "state",
        "dist",
        "node_modules",
    }
)
EXCLUDED_DIRECTORIES = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"})
EXCLUDED_SUFFIX = ".sync-merge"
FILE_MODES = {"100644": 0o644, "100755": 0o755}
SYMLINK_MODE = "120000"
DEFAULT_MESSAGE = "Initial public release"
AUTHOR = re.compile(r"\s*([^<>]*[^<>\s])\s*<([^<>\s]+)>\s*")
IDENTITY_VARIABLES = frozenset(
    f"GIT_{role}_{field}" for role in ("AUTHOR", "COMMITTER") for field in ("NAME", "EMAIL")
)
# No hook runs in either repository, whatever the global configuration says: every git call,
# including git add and git commit in the destination, passes this.
NO_HOOKS = ("-c", "core.hooksPath=/dev/null")
MAX_LINK_HOPS = 40


class ExportError(Exception):
    """A refusal; the message must not contain a forbidden string."""


def git_environment(identity=None):
    """The environment for every git call: no inherited GIT_* variable except the four identity
    variables, which `identity` overrides.

    Git sets GIT_DIR, GIT_INDEX_FILE, and others inside hooks, and any of them would point
    a command at another repository. The identity variables only name the author and
    committer, so an operator's exported public identity reaches the commit.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") or key in IDENTITY_VARIABLES
    }
    for key, value in (identity or {}).items():
        if key not in IDENTITY_VARIABLES:
            raise ValueError(f"{key} is not an identity variable")
        env[key] = value
    return env


def git(repo, *args, data=None, identity=None):
    result = subprocess.run(
        ["git", *NO_HOOKS, "-C", str(repo), *args],
        input=data,
        capture_output=True,
        env=git_environment(identity),
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        command = next((arg for arg in args if not arg.startswith("-") and "=" not in arg), "")
        raise ExportError(f"git {command} failed: {detail}")
    return result.stdout


def load_patterns(path):
    """Return (line number, casefolded string) for every non-blank line of the forbidden file."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ExportError(
            f"cannot read the forbidden file: {getattr(error, 'strerror', None) or error}"
        ) from None
    patterns = [(number, line.strip().casefold()) for number, line in enumerate(lines, 1) if line.strip()]
    if not patterns:
        raise ExportError("the forbidden file lists no strings")
    return patterns


def matching(text, patterns):
    folded = text.casefold()
    return [number for number, pattern in patterns if pattern in folded]


def redact(text, patterns):
    """Replace every forbidden string in text, or withhold the text if one still matches."""
    for number, pattern in patterns:
        text = re.sub(re.escape(pattern), f"<pattern {number}>", text, flags=re.IGNORECASE)
    return "<withheld>" if matching(text, patterns) else text


def content_hits(data, patterns):
    """Return (line number, pattern line) for each forbidden string in data."""
    text = data.decode("utf-8", "surrogateescape").casefold()
    if not any(pattern in text for _, pattern in patterns):
        return []
    return [
        (line_number, number)
        for line_number, line in enumerate(text.split("\n"), 1)
        for number, pattern in patterns
        if pattern in line
    ]


def tree_entries(repo, commit):
    """Yield (mode, type, object id, path) for every entry of the commit's tree."""
    for record in git(repo, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if record:
            meta, _, name = record.partition(b"\t")
            mode, kind, oid = meta.decode("ascii").split(" ")
            yield mode, kind, oid, os.fsdecode(name)


def is_excluded(parts):
    return (
        parts[0] in EXCLUDED_ROOTS
        or any(part in EXCLUDED_DIRECTORIES for part in parts[:-1])
        or parts[-1].endswith(EXCLUDED_SUFFIX)
    )


def safe_parts(path):
    parts = PurePosixPath(path).parts
    if (
        not parts
        or path.startswith("/")
        or any(part in ("", ".", "..") or part.lower() == ".git" for part in parts)
    ):
        raise ExportError(f"unsafe path in the tree: {path!r}")
    return parts


def link_stays_inside(path, target, links):
    """True when the link resolves inside the export, following the other planned links hop by hop.

    `links` maps every planned symbolic link to its target. A `..` after a link to a
    directory climbs from where that link points, as the file system would, so a chain
    such as `d/l1 -> ..` and `l2 -> d/l1/../..` is caught.
    """
    current = path.split("/")[:-1]
    pending = []
    for _ in range(MAX_LINK_HOPS):
        if not target or target.startswith("/") or "\0" in target:
            return False
        pending[:0] = target.split("/")
        target = None
        while pending and target is None:
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                if not current:
                    return False
                current.pop()
            elif part.lower() == ".git":
                return False
            elif "/".join([*current, part]) in links:
                # Continue from the directory holding that link, with its target in front.
                target = links["/".join([*current, part])]
            else:
                current.append(part)
        if target is None:
            return True
    return False


class Blobs:
    """Read blobs through one long-running git cat-file --batch process."""

    def __init__(self, repo):
        self.process = subprocess.Popen(
            ["git", *NO_HOOKS, "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=git_environment(),
        )

    def read(self, oid):
        self.process.stdin.write(oid.encode("ascii") + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise ExportError(f"cannot read blob {oid}")
        data = self.process.stdout.read(int(header[2]))
        self.process.stdout.read(1)
        return data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.wait()


def plan(repo, commit):
    """Return the kept entries as (mode, oid, path) and the excluded entry count."""
    kept, excluded = [], 0
    for mode, kind, oid, path in tree_entries(repo, commit):
        parts = safe_parts(path)
        if is_excluded(parts):
            excluded += 1
        elif kind != "blob" or (mode not in FILE_MODES and mode != SYMLINK_MODE):
            raise ExportError(f"unsupported {kind} entry with mode {mode}: {path}")
        else:
            kept.append((mode, oid, path))
    if not kept:
        raise ExportError("the commit has no files to export")
    return kept, excluded


def write_tree(repo, entries, destination, patterns):
    """Write every entry into destination and return the forbidden-string findings."""
    findings, links = [], []
    with Blobs(repo) as blobs:
        for mode, oid, path in entries:
            findings += [f"{path}: path name matches forbidden line {n}" for n in matching(path, patterns)]
            data = blobs.read(oid)
            if mode == SYMLINK_MODE:
                links.append((path, os.fsdecode(data)))
                continue
            findings += [
                f"{path}:{line}: matches forbidden line {n}" for line, n in content_hits(data, patterns)
            ]
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            with open(os.open(target, flags, FILE_MODES[mode]), "wb") as handle:
                handle.write(data)
                os.fchmod(handle.fileno(), FILE_MODES[mode])
    planned = dict(links)
    for path, link in links:
        findings += [f"{path}: link target matches forbidden line {n}" for n in matching(link, patterns)]
        if not link_stays_inside(path, link, planned):
            raise ExportError(f"symlink {redact(path, patterns)} points outside the export")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(link, target)
    return findings


def listed_entries(data, oid_field):
    """Map each path in `ls-files -s -z` or `ls-tree -r -z` output to its (mode, object id).

    The object id is field 1 of `ls-files -s` (mode, id, stage) and field 2 of `ls-tree`
    (mode, type, id).
    """
    listed = {}
    for record in data.split(b"\0"):
        if record:
            meta, _, name = record.partition(b"\t")
            fields = meta.decode("ascii").split(" ")
            listed[os.fsdecode(name)] = (fields[0], fields[oid_field])
    return listed


def require_entries(listed, expected, what, patterns):
    """Refuse when Git recorded anything other than the exported entries; name the paths."""
    if listed == expected:
        return
    for path in sorted(set(listed) | set(expected)):
        if path not in expected:
            print(f"differs: {redact(path, patterns)}: {what} but not exported", file=sys.stderr)
        elif path not in listed:
            print(f"differs: {redact(path, patterns)}: exported but not {what}", file=sys.stderr)
        elif listed[path] != expected[path]:
            print(f"differs: {redact(path, patterns)}: {what} with other content or mode", file=sys.stderr)
    raise ExportError(f"the {what} files differ from the exported tree")


def commit_identity(author):
    """The identity variables for the commit: from --author, or the inherited ones when all are set.

    Git would otherwise fill a missing name or email from ~/.gitconfig, which may hold a
    private identity, so the tool refuses instead.
    """
    if author is None:
        missing = sorted(key for key in IDENTITY_VARIABLES if not os.environ.get(key))
        if missing:
            raise ExportError(
                'the author must be given explicitly: pass --author "Name <email>", or set '
                + ", ".join(sorted(IDENTITY_VARIABLES))
                + " (missing: "
                + ", ".join(missing)
                + ")"
            )
        return {}
    match = AUTHOR.fullmatch(author)
    if not match:
        raise ExportError('--author must look like "Name <email>"')
    identity = {}
    for role in ("AUTHOR", "COMMITTER"):
        identity[f"GIT_{role}_NAME"], identity[f"GIT_{role}_EMAIL"] = match[1], match[2]
    return identity


def commit_tree(destination, entries, message, identity, patterns=()):
    expected = {path: (mode, oid) for mode, oid, path in entries}
    # No template, so no sample or configured hooks land in the new repository.
    git(destination, "init", "-q", "--template=", "-b", "main")
    git(destination, "-c", "core.autocrlf=false", "add", "--all", "--force")
    staged = listed_entries(git(destination, "ls-files", "-s", "-z"), 1)
    require_entries(staged, expected, "staged", patterns)
    git(
        destination,
        "commit",
        "-q",
        "--no-verify",
        "-F",
        "-",
        data=message.encode("utf-8"),
        identity=identity,
    )
    committed = listed_entries(git(destination, "ls-tree", "-r", "-z", "--full-tree", "HEAD"), 2)
    require_entries(committed, expected, "committed", patterns)
    return (
        git(destination, "rev-parse", "HEAD^{tree}").decode().strip(),
        git(destination, "rev-parse", "HEAD").decode().strip(),
    )


def clear(destination, created):
    if created:
        shutil.rmtree(destination, ignore_errors=True)
        return
    for child in destination.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def export(args, patterns):
    repo = Path(git(args.source, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    try:
        commit = git(repo, "rev-parse", "--verify", "--quiet", f"{args.commit}^{{commit}}").decode().strip()
    except ExportError:
        raise ExportError(f"{args.commit} does not name a commit in the source repository") from None
    try:
        head = git(repo, "rev-parse", "--verify", "--quiet", "HEAD^{commit}").decode().strip()
    except ExportError:
        head = None
    if commit == head and git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise ExportError(
            "the source worktree has uncommitted changes to the exported commit; commit or discard them"
        )
    identity = commit_identity(args.author)
    destination = Path(args.destination).resolve()
    if destination.is_relative_to(repo):
        raise ExportError("the destination is inside the source repository")
    created = not destination.exists()
    if not created and (not destination.is_dir() or any(destination.iterdir())):
        raise ExportError("the destination exists and is not an empty directory")
    entries, excluded = plan(repo, commit)
    message = args.message
    if args.trailer:
        message = message.rstrip("\n") + "\n\n" + "\n".join(args.trailer)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        findings = write_tree(repo, entries, destination, patterns)
        if findings:
            for finding in findings:
                print(f"forbidden: {redact(finding, patterns)}", file=sys.stderr)
            raise ExportError(f"{len(findings)} forbidden-string match(es); nothing was exported")
        tree, commit_id = commit_tree(destination, entries, message + "\n", identity, patterns)
        commit_text = git(destination, "cat-file", "commit", "HEAD").decode("utf-8", "surrogateescape")
        lines = matching(commit_text, patterns)
        if lines:
            raise ExportError(f"the commit message or identity matches forbidden line(s) {lines}")
    except BaseException:
        clear(destination, created)
        raise
    return {"tree": tree, "commit": commit_id, "files": len(entries), "excluded": excluded}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, help="the private repository")
    parser.add_argument("--commit", required=True, help="the commit to export")
    parser.add_argument("--destination", required=True, help="a new or empty directory outside the source")
    parser.add_argument("--forbidden", required=True, help="a file with one forbidden string per line")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--author", help='"Name <email>" for the author and committer')
    parser.add_argument("--trailer", action="append", default=[], help="a trailer line; repeat as needed")
    args = parser.parse_args(argv)
    patterns = []
    try:
        patterns = load_patterns(args.forbidden)
        result = export(args, patterns)
    except (ExportError, OSError) as error:
        print(
            f"export_public_tree: refused: {redact(str(error), patterns) if patterns else error}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
