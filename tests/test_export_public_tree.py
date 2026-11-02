"""tools/export_public_tree.py exports one commit into a clean single-commit repository."""

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/export_public_tree.py"
SECRET = "Zorblatt"
AUTHOR = "Public Maintainer <maintainer@example.invalid>"
IDENTITY = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.invalid",
}


SPEC = importlib.util.spec_from_file_location("export_public_tree", TOOL)
export_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_tool)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


class ExportPublicTreeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        # The tool drops GIT_CONFIG_GLOBAL, so its git calls read $HOME/.gitconfig and
        # $XDG_CONFIG_HOME/git/config; an empty home keeps the developer's commit.gpgSign,
        # core.fsmonitor, or filter drivers out of the tests.
        self.home = self.base / "home"
        self.home.mkdir()
        (self.home / ".gitconfig").write_text("", encoding="utf-8")
        isolated = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")}
        # Git sets repository-locating variables such as GIT_DIR inside hooks; none may leak in.
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        patcher = mock.patch.dict(os.environ, {**environment, **isolated, **IDENTITY}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = self.base / "source"
        self.forbidden = self.base / "forbidden.txt"
        self.forbidden.write_text(f"\n{SECRET.lower()}\n", encoding="utf-8")
        self.files = {
            "README.md": ("Public readme\n", 0o644),
            "tools/run.sh": ("#!/bin/sh\necho run\n", 0o755),
            "src/pkg/module.py": ("VALUE = 1\n", 0o644),
            ".private/secret": (f"{SECRET} lives here\n", 0o644),
            "src/pkg/module.py.sync-merge": (f"{SECRET} merge leftovers\n", 0o644),
            "artifacts/report.txt": ("generated\n", 0o644),
        }
        self.commit_files(self.files)
        (self.source / "docs").mkdir()
        os.symlink("../README.md", self.source / "docs/readme-link.md")
        self.commit()

    def commit_files(self, files):
        for path, (text, mode) in files.items():
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            target.chmod(mode)
        if not (self.source / ".git").exists():
            git(self.base, "init", "-q", "-b", "main", str(self.source))

    def commit(self, message="change"):
        git(self.source, "add", "--all", "--force")
        git(self.source, "commit", "-q", "-m", message)

    def run_tool(self, destination, *extra, commit="HEAD", env=None, author=AUTHOR):
        """Run the tool with --author unless author is None or extra already passes one."""
        command = [sys.executable, str(TOOL), "--source", str(self.source), "--commit", commit]
        command += ["--destination", str(destination), "--forbidden", str(self.forbidden)]
        if author is not None and "--author" not in extra:
            command += ["--author", author]
        return subprocess.run(command + list(extra), capture_output=True, text=True, env=env)

    def assertRefused(self, result, destination, text):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(text, result.stderr)
        self.assertNotIn(SECRET.lower(), (result.stdout + result.stderr).lower())
        self.assertFalse(destination.exists(), "a refused export leaves no destination")

    def test_export_writes_one_clean_commit(self):
        destination = self.base / "public"
        result = self.run_tool(
            destination,
            "--trailer",
            "Co-Authored-By: Helper <helper@example.invalid>",
            "--trailer",
            "Claude-Session: https://example.invalid/session",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["files"], 4)
        self.assertEqual(summary["excluded"], 3)
        written = sorted(
            str(path.relative_to(destination))
            for path in destination.rglob("*")
            if ".git" not in path.relative_to(destination).parts and not path.is_dir()
        )
        self.assertEqual(written, ["README.md", "docs/readme-link.md", "src/pkg/module.py", "tools/run.sh"])
        self.assertFalse((destination / ".private").exists())
        self.assertEqual(git(destination, "rev-list", "--count", "HEAD").strip(), b"1")
        self.assertEqual(git(destination, "rev-parse", "HEAD").decode().strip(), summary["commit"])
        self.assertEqual(git(destination, "rev-parse", "HEAD^{tree}").decode().strip(), summary["tree"])
        self.assertEqual(git(destination, "status", "--porcelain"), b"", "the tree matches the written files")
        source_tree = git(self.source, "ls-tree", "-r", "HEAD").decode().splitlines()
        public_tree = git(destination, "ls-tree", "-r", "HEAD").decode().splitlines()
        self.assertEqual(public_tree, [line for line in source_tree if line.split("\t")[1] in written])
        log = git(destination, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%B").decode()
        identity, committer, message = log.split("|", 2)
        self.assertEqual(identity, AUTHOR)
        self.assertEqual(committer, identity)
        self.assertEqual(
            message.strip(),
            "Initial public release\n\nCo-Authored-By: Helper <helper@example.invalid>\n"
            "Claude-Session: https://example.invalid/session",
        )

    def test_file_modes_and_symlinks_are_preserved(self):
        destination = self.base / "public"
        self.assertEqual(self.run_tool(destination).returncode, 0)
        self.assertEqual(stat.S_IMODE((destination / "tools/run.sh").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((destination / "README.md").stat().st_mode), 0o644)
        link = destination / "docs/readme-link.md"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "../README.md")
        self.assertIn("120000", git(destination, "ls-tree", "HEAD", "docs/readme-link.md").decode())

    def test_untracked_files_never_reach_the_export(self):
        commit = git(self.source, "rev-parse", "HEAD").decode().strip()
        (self.source / "untracked.txt").write_text("not committed\n", encoding="utf-8")
        destination = self.base / "public"
        self.assertEqual(self.run_tool(destination, commit=commit).returncode, 0)
        self.assertFalse((destination / "untracked.txt").exists())

    def test_uncommitted_changes_refuse_a_head_export(self):
        (self.source / "README.md").write_text("edited\n", encoding="utf-8")
        destination = self.base / "public"
        self.assertRefused(self.run_tool(destination), destination, "uncommitted changes")

    def test_forbidden_string_in_a_file_refuses(self):
        # The secret sits on line 3, so the report cannot confuse it with forbidden line 2.
        self.commit_files(
            {"docs/notes.txt": (f"first line\nsecond line\nhosted by {SECRET.upper()}\n", 0o644)}
        )
        self.commit()
        destination = self.base / "public"
        result = self.run_tool(destination)
        self.assertRefused(result, destination, "docs/notes.txt:3: matches forbidden line 2")

    def test_forbidden_string_in_binary_content_refuses(self):
        target = self.source / "assets/blob.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\x00\xff\xfe" + SECRET.encode() + b"\x00\x01")
        self.commit()
        destination = self.base / "public"
        self.assertRefused(
            self.run_tool(destination), destination, "assets/blob.bin:1: matches forbidden line 2"
        )

    def test_forbidden_string_in_a_symlink_target_refuses(self):
        os.symlink(f"../{SECRET}.md", self.source / "docs/pointer.md")
        self.commit()
        destination = self.base / "public"
        self.assertRefused(
            self.run_tool(destination), destination, "docs/pointer.md: link target matches forbidden line 2"
        )

    def test_submodule_entry_refuses(self):
        head = git(self.source, "rev-parse", "HEAD").decode().strip()
        # An empty directory stands for the unpopulated submodule, so the worktree stays clean.
        (self.source / "vendor/library").mkdir(parents=True)
        git(self.source, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor/library")
        git(self.source, "commit", "-q", "-m", "add a submodule entry")
        destination = self.base / "public"
        self.assertRefused(
            self.run_tool(destination),
            destination,
            "unsupported commit entry with mode 160000: vendor/library",
        )

    def test_chained_symlinks_that_climb_out_refuse(self):
        os.symlink("..", self.source / "docs/up")
        os.symlink("docs/up/../..", self.source / "escape")
        self.commit()
        destination = self.base / "public"
        self.assertRefused(self.run_tool(destination), destination, "points outside the export")

    def test_inherited_git_variables_cannot_redirect_the_export(self):
        decoy = self.base / "decoy"
        git(self.base, "init", "-q", "-b", "main", str(decoy))
        (decoy / "decoy.txt").write_text("decoy\n", encoding="utf-8")
        git(decoy, "add", "decoy.txt")
        git(decoy, "commit", "-q", "-m", "decoy")
        before = git(decoy, "rev-parse", "HEAD")
        environment = {
            **os.environ,
            "GIT_DIR": str(decoy / ".git"),
            "GIT_WORK_TREE": str(decoy),
            "GIT_INDEX_FILE": str(decoy / ".git/index"),
            "GIT_OBJECT_DIRECTORY": str(decoy / ".git/objects"),
            "GIT_COMMON_DIR": str(decoy / ".git"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(self.base / "hooks"),
        }
        destination = self.base / "public"
        result = self.run_tool(destination, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["files"], 4)
        self.assertEqual(git(decoy, "rev-parse", "HEAD"), before)
        self.assertEqual(git(decoy, "rev-list", "--count", "HEAD").strip(), b"1")
        self.assertFalse((destination / "decoy.txt").exists())

    def test_hooks_never_run_in_the_destination(self):
        home = self.base / "home"
        hooks = home / "hooks"
        templates = home / "template/hooks"
        marker = self.base / "hook-ran"
        for directory in (hooks, templates):
            directory.mkdir(parents=True)
            for name in ("pre-commit", "commit-msg", "post-commit", "post-index-change"):
                script = directory / name
                script.write_text(f"#!/bin/sh\necho {name} >> {marker}\nexit 1\n", encoding="utf-8")
                script.chmod(0o755)
        (home / ".gitconfig").write_text(
            f"[core]\n\thooksPath = {hooks}\n[init]\n\ttemplateDir = {home / 'template'}\n", encoding="utf-8"
        )
        environment = {key: value for key, value in os.environ.items() if key != "GIT_CONFIG_GLOBAL"}
        environment["HOME"] = str(home)
        destination = self.base / "public"
        result = self.run_tool(destination, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists(), "a hook ran")
        # An empty template copies nothing, so no hooks directory exists to run from later.
        self.assertFalse((destination / ".git/hooks").exists())

    def test_inherited_identity_variables_name_the_author_without_the_option(self):
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = Configured Person\n\temail = configured@example.invalid\n", encoding="utf-8"
        )
        destination = self.base / "public"
        result = self.run_tool(destination, author=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        log = git(destination, "log", "-1", "--format=%an <%ae>|%cn <%ce>").decode().strip()
        self.assertEqual(log, "Test Author <author@example.invalid>|Test Author <author@example.invalid>")

    def test_a_missing_identity_without_the_option_refuses(self):
        # Git would fill the gaps from this file; the tool must refuse instead.
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = Configured Person\n\temail = configured@example.invalid\n", encoding="utf-8"
        )
        for missing in (export_tool.IDENTITY_VARIABLES, {"GIT_COMMITTER_EMAIL"}):
            with self.subTest(missing=missing):
                environment = {key: value for key, value in os.environ.items() if key not in missing}
                destination = self.base / "public"
                result = self.run_tool(destination, env=environment, author=None)
                self.assertRefused(result, destination, "the author must be given explicitly")
                self.assertNotIn("Configured Person", result.stderr)

    def test_forbidden_string_in_a_path_refuses(self):
        self.commit_files({f"docs/{SECRET}-notes.txt": ("harmless\n", 0o644)})
        self.commit()
        destination = self.base / "public"
        result = self.run_tool(destination)
        self.assertRefused(
            result, destination, "docs/<pattern 2>-notes.txt: path name matches forbidden line 2"
        )

    def test_forbidden_string_in_the_message_refuses(self):
        destination = self.base / "public"
        result = self.run_tool(destination, "--message", f"Release by {SECRET}")
        self.assertRefused(result, destination, "commit message or identity")

    def test_symlink_leaving_the_export_refuses(self):
        os.symlink("../../outside", self.source / "docs/escape")
        self.commit()
        destination = self.base / "public"
        self.assertRefused(self.run_tool(destination), destination, "points outside the export")

    def test_a_refused_export_leaves_an_existing_empty_destination_empty(self):
        self.commit_files({"docs/notes.txt": (f"one\ntwo\nthree {SECRET}\n", 0o644)})
        self.commit()
        destination = self.base / "public"
        destination.mkdir()
        result = self.run_tool(destination)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("forbidden-string match", result.stderr)
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])

    def test_a_named_commit_equal_to_head_still_refuses_uncommitted_changes(self):
        head = git(self.source, "rev-parse", "HEAD").decode().strip()
        (self.source / "README.md").write_text("edited\n", encoding="utf-8")
        destination = self.base / "public"
        for commit in (head, "main"):
            with self.subTest(commit=commit):
                self.assertRefused(
                    self.run_tool(destination, commit=commit), destination, "uncommitted changes"
                )

    def test_differing_entries_are_named_in_both_directions_without_forbidden_text(self):
        patterns = export_tool.load_patterns(self.forbidden)
        expected = {"README.md": ("100644", "a" * 40), f"docs/{SECRET}.md": ("100644", "b" * 40)}
        listed = {"README.md": ("100755", "a" * 40), "extra.txt": ("100644", "c" * 40)}
        errors = io.StringIO()
        with (
            contextlib.redirect_stderr(errors),
            self.assertRaisesRegex(export_tool.ExportError, "staged files"),
        ):
            export_tool.require_entries(listed, expected, "staged", patterns)
        report = errors.getvalue()
        self.assertIn("differs: README.md: staged with other content or mode", report)
        self.assertIn("differs: extra.txt: staged but not exported", report)
        self.assertIn("differs: docs/<pattern 2>.md: exported but not staged", report)
        self.assertNotIn(SECRET.lower(), report.lower())

    def test_existing_non_empty_destination_refuses(self):
        destination = self.base / "public"
        destination.mkdir()
        (destination / "keep.txt").write_text("keep\n", encoding="utf-8")
        result = self.run_tool(destination)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not an empty directory", result.stderr)
        self.assertEqual(sorted(path.name for path in destination.iterdir()), ["keep.txt"])

    def test_destination_inside_the_source_refuses(self):
        destination = self.source / "export"
        self.assertRefused(self.run_tool(destination), destination, "inside the source repository")


if __name__ == "__main__":
    unittest.main()
