"""The site's name, home heading, description, support link, author, and About text, from publisher.json.

`site_name` names the site in titles, the footer, the error pages, and the
social images; a production configuration must set it, and elsewhere it
defaults to DEFAULT_NAME. `site_tagline` is the home page heading,
`site_description` the line under it, in the footer, and in the home page's
meta description, `support_link` an optional link to a support or donation page
({"url": ..., "label": ...}), and `author_name` the name the author page and
story bylines show. `about_file` names a Markdown file, which may hold HTML
blocks, whose text replaces the About page's generic introduction. The theme
tools read them from the environment variables in ENVIRONMENT, SUPPORT_URL,
SUPPORT_LABEL, and ABOUT_ENVIRONMENT, which export() sets for every process the
publisher starts; tools/ghost-theme/site-settings.mjs applies the same rules
and holds the defaults for the optional ones.

This module uses only the standard library and imports nothing else from the
package, so the theme tools can load it from a plain checkout.
"""

import hashlib
import os
import re
import stat
from pathlib import Path

DEFAULT_NAME = "ADS-B findings"
ENVIRONMENT = {
    "site_name": "SITE_NAME",
    "site_tagline": "SITE_TAGLINE",
    "site_description": "SITE_DESCRIPTION",
    "author_name": "SITE_AUTHOR",
}
SUPPORT_URL = "SITE_SUPPORT_URL"
SUPPORT_LABEL = "SITE_SUPPORT_LABEL"
# Longest allowed value, in characters, of each text setting.
LIMITS = {"site_name": 60, "site_tagline": 120, "site_description": 160, "author_name": 60, "label": 40}
URL_LIMIT = 2000
ABOUT_ENVIRONMENT = "SITE_ABOUT_FILE"
# Largest About file, in bytes.
ABOUT_LIMIT = 64 * 1024
# Control characters, including the C1 range and the Unicode line and paragraph separators, and
# the byte order mark, which JavaScript's trim() removes and Python's strip() keeps.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff]")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# An https:// address with a lower-case host name (no IP address, port, or user) and an
# optional path, query, or fragment of plain URL characters.
URL_FORM = re.compile(
    rf"https://(?:{_LABEL}\.)*(?=[a-z]){_LABEL}(?:[/?#][A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]*)?"
)


def validate_text(value, setting, limit):
    """`value` when it is 1 to `limit` characters of one-line text with no surrounding spaces."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _CONTROL.search(value)
        or len(value) > limit
    ):
        raise ValueError(
            f"{setting} must be 1 to {limit} characters on one line, with no leading or trailing spaces"
        )
    return value


def validate_author(value, setting="author_name"):
    """An author name, which must also hold a letter or digit for the author page's address."""
    validate_text(value, setting, LIMITS["author_name"])
    if not re.search(r"[A-Za-z0-9]", value):
        raise ValueError(f"{setting} must contain a letter or digit from A to Z or 0 to 9")
    return value


def validate_url(value, setting):
    """`value` when it is an https:// address with a lower-case host name and no port or user."""
    if not isinstance(value, str) or not URL_FORM.fullmatch(value) or len(value) > URL_LIMIT:
        raise ValueError(
            f"{setting} must be an https:// address such as https://example.com/support, "
            "with a lower-case host name and no port"
        )
    return value


def validate_support(value):
    """`support_link`: an object with exactly a `url` and a `label`."""
    if not isinstance(value, dict) or set(value) != {"url", "label"}:
        raise ValueError('support_link must be an object with "url" and "label"')
    return {
        "url": validate_url(value["url"], "support_link.url"),
        "label": validate_text(value["label"], "support_link.label", LIMITS["label"]),
    }


def about_file(value, setting="about_file"):
    """The About file's path, after checking it holds 1 byte to ABOUT_LIMIT bytes of UTF-8 text.

    None when the path is /dev/null, which the container template mounts when no file is set.
    """
    if not isinstance(value, str) or not Path(value).is_absolute() or value != value.strip():
        raise ValueError(f"{setting} must be an absolute path")
    try:
        info = os.stat(value)
        # A bind-mounted /dev/null is another inode of the same device.
        if stat.S_ISCHR(info.st_mode) and info.st_rdev == os.stat(os.devnull).st_rdev:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{setting} must name a regular file")
        with open(value, "rb") as stream:
            data = stream.read(ABOUT_LIMIT + 1)
    except OSError as error:
        raise ValueError(f"{setting} cannot be read ({type(error).__name__})") from None
    if len(data) > ABOUT_LIMIT:
        raise ValueError(f"{setting} must be at most {ABOUT_LIMIT} bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{setting} must be UTF-8 text") from None
    if not text.strip():
        raise ValueError(f"{setting} is empty; delete the key to keep the generic About text")
    return value


def about_sha256(config):
    """The SHA-256 of the configured About file's bytes; None when no file is set."""
    value = (config or {}).get("about_file")
    path = about_file(value) if value is not None else None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None


def configured_name():
    """The site name the environment names; None when it names none."""
    value = os.environ.get(ENVIRONMENT["site_name"])
    return validate_text(value, ENVIRONMENT["site_name"], LIMITS["site_name"]) if value else None


def export(config):
    """Check the identity settings and name them in this process's environment, for the tools it starts.

    A production configuration must set site_name. An about_file of /dev/null counts as unset.
    """
    config = config or {}
    if config.get("site_name") is None and config.get("production") is True:
        raise ValueError("site_name is required when production is true")
    values = {}
    for key, variable in ENVIRONMENT.items():
        value = config.get(key)
        if value is not None:
            values[variable] = (
                validate_author(value) if key == "author_name" else validate_text(value, key, LIMITS[key])
            )
    if config.get("support_link") is not None:
        support = validate_support(config["support_link"])
        values[SUPPORT_URL] = support["url"]
        values[SUPPORT_LABEL] = support["label"]
    if config.get("about_file") is not None and (path := about_file(config["about_file"])):
        values[ABOUT_ENVIRONMENT] = path
    os.environ.update(values)
