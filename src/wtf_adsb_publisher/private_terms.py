r"""Names no public text may contain: built-in generic patterns plus the operator's own.

The built-in patterns (Tailscale names and addresses, auth keys, API keys, the
private directory, and receiver field names) always apply. A site adds its own
names, users, and hosts through `private_terms_file` in publisher.json, a file
holding one regular expression on one line, and through `private_terms`, a list
of literal strings. Tools that run without the configuration, such as the theme
build and checks, find the same file through the PRIVATE_TERMS_FILE environment
variable, which the publisher sets for every process it starts.

The Node build checks read the file too, so its expression must use syntax that
Python and JavaScript share: no `(?P<name>...)`, `(?>...)`, `(?#...)`, `\A`, or
`\Z`, and no global flags such as `(?i)`; matching already ignores case. The
literal `private_terms` list applies only in the Python checks and in
`verify-live.mjs --config`, not in the Node build checks.

This module uses only the standard library and imports nothing else from the
package, so the theme tools can load it from a plain checkout.
"""

import os
import re
from pathlib import Path

ENVIRONMENT = "PRIVATE_TERMS_FILE"
BUILTIN_FILE = Path(__file__).resolve().parent / "data" / "private-patterns.txt"


def builtin():
    """The generic patterns every check applies, whatever the configuration."""
    return BUILTIN_FILE.read_text().strip()


def read_file(path):
    """The single regular expression in an operator's terms file.

    An empty file is refused: an operator who wants no terms of their own deletes
    the key. So is an expression that matches the empty string, which would
    refuse every release.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("private_terms_file must be an absolute path")
    # A byte order mark is not part of the first term; JavaScript's trim() drops it too.
    value = path.read_text(encoding="utf-8-sig").strip()
    if not value:
        raise ValueError("private_terms_file is empty; delete the key to add no terms")
    if "\n" in value:
        raise ValueError("private_terms_file must hold one regular expression on one line")
    if _compile(value, "private_terms_file is not a valid regular expression").search(""):
        raise ValueError("private_terms_file matches the empty string, so it would refuse every text")
    return value


def _compile(value, message):
    try:
        return re.compile(value, re.I)
    except re.error as error:
        raise ValueError(message) from error


def terms_file(config=None):
    """The configured terms file, or the one the environment names; None when neither does."""
    value = (config or {}).get("private_terms_file") or os.environ.get(ENVIRONMENT)
    if value is not None and not isinstance(value, str):
        raise ValueError("private_terms_file must be a path string")
    return value or None


def pattern(config=None):
    """Built-in patterns, the terms file's expression, and each literal term, as one expression."""
    config = config or {}
    terms = config.get("private_terms", [])
    if not isinstance(terms, list) or not all(isinstance(term, str) for term in terms):
        raise ValueError("private_terms must be a list of strings")
    parts = [builtin()]
    if path := terms_file(config):
        parts.append(read_file(path))
    parts.extend(re.escape(term) for term in terms if term)
    joined = "|".join(parts)
    # The file's expression can be valid alone and not after the built-in patterns,
    # as a leading global flag such as (?i) is.
    _compile(joined, "private_terms_file does not join the built-in patterns as a valid regular expression")
    return joined


def compiled(config=None):
    """`pattern` compiled to match in any case."""
    return re.compile(pattern(config), re.I)


def export(config):
    """Name the configured terms file in this process's environment, for the tools it starts.

    The joined pattern is built here, so a bad file fails at load time rather than
    inside a tool the publisher starts.
    """
    path = (config or {}).get("private_terms_file")
    if path:
        if not isinstance(path, str):
            raise ValueError("private_terms_file must be a path string")
        pattern(config)
        os.environ[ENVIRONMENT] = path
