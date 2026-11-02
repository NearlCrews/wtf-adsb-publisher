"""The public site's origin, from `site_origin` in publisher.json.

Canonical links, feeds, sitemaps, structured data, the links owned article
blocks make, and the User-Agent of outside requests all name this origin. A
production configuration must set it; elsewhere it defaults to EXAMPLE. Tools
that run without the configuration, such as the theme build and checks, read
the SITE_ORIGIN environment variable, which the publisher sets for every
process it starts. The optional public contact address, `contact_email`, travels
the same way as SITE_CONTACT_EMAIL; without it, pages name no address.

This module uses only the standard library and imports nothing else from the
package, so the theme tools can load it from a plain checkout.
"""

import os
import re
from urllib.parse import urlsplit

ENVIRONMENT = "SITE_ORIGIN"
EXAMPLE = "https://example.com"
# The User-Agent product, and the contact it names when no origin is set.
PRODUCT = "WTF-ADSB/1.0"
FALLBACK_CONTACT = "https://github.com/NearlCrews/wtf-adsb-publisher"
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# The last label starts with a letter, so IP addresses such as https://192.0.2.1
# and https://0x7f000001 are refused.
FORM = re.compile(rf"https://(?:{_LABEL}\.)*(?=[a-z]){_LABEL}")
CONTACT_ENVIRONMENT = "SITE_CONTACT_EMAIL"
# A plain address: dot-separated local-part atoms, then a domain with at least one dot.
_ATOM = r"[A-Za-z0-9_%+-]+"
_DOMAIN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
CONTACT_FORM = re.compile(rf"{_ATOM}(?:\.{_ATOM})*@(?:{_DOMAIN_LABEL}\.)+{_DOMAIN_LABEL}")


def validate(value, setting="site_origin"):
    """`value` when it is a lower-case https:// origin with no port, path, or trailing slash.

    The error names `setting`, the place the value came from.
    """
    if not isinstance(value, str) or not FORM.fullmatch(value) or len(urlsplit(value).hostname) > 253:
        raise ValueError(
            f"{setting} must be an https:// origin such as https://example.com, "
            "in lower case, with no port, path, or trailing slash"
        )
    return value


def configured(config=None):
    """The configured origin, or the one the environment names; None when neither does."""
    value = (config or {}).get("site_origin")
    if value:
        return validate(value)
    value = os.environ.get(ENVIRONMENT)
    return validate(value, ENVIRONMENT) if value else None


def get(config=None):
    """The public origin: the configured one, else EXAMPLE."""
    return configured(config) or EXAMPLE


def hosts(config=None):
    """The origin's host and its www. form, the names under which the site serves itself."""
    host = urlsplit(get(config)).hostname.removeprefix("www.")
    return frozenset({host, "www." + host})


def user_agent(product=PRODUCT, config=None):
    """A User-Agent naming the site's about page, or the project page when no origin is set."""
    origin = configured(config)
    return f"{product} (+{origin}/about/)" if origin else f"{product} (+{FALLBACK_CONTACT})"


def validate_contact(value, setting="contact_email"):
    """`value` when it is a plain email address, such as contact@example.com, with no name or brackets."""
    if (
        not isinstance(value, str)
        or not CONTACT_FORM.fullmatch(value)
        or len(value) > 254
        or len(value.split("@")[0]) > 64
    ):
        raise ValueError(f"{setting} must be a plain email address such as contact@example.com")
    return value


def contact_email(config=None):
    """The configured contact address, or the one the environment names; None when neither does."""
    value = (config or {}).get("contact_email")
    if value is not None:
        return validate_contact(value)
    value = os.environ.get(CONTACT_ENVIRONMENT)
    return validate_contact(value, CONTACT_ENVIRONMENT) if value else None


def export_contact(config):
    """Check the configured contact address and name it in this process's environment."""
    value = (config or {}).get("contact_email")
    if value is not None:
        os.environ[CONTACT_ENVIRONMENT] = validate_contact(value)


def export(config):
    """Check the configured origin and name it in this process's environment, for the tools it starts.

    A production configuration must set one.
    """
    config = config or {}
    value = config.get("site_origin")
    if value is None:
        if config.get("production") is True:
            raise ValueError("site_origin is required when production is true")
        return
    os.environ[ENVIRONMENT] = validate(value)
