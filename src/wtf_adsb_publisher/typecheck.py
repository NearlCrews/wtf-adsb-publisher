"""Validate imported aircraft-type checks against their recorded sources.

The engine queries aircraft registries. The publisher retains the comparison
rules for type-dependent findings and holds evidence whose second source
disagrees, except a mismatch story, which names the registry's type instead. It
does not run a registry worker or open the receiver's journal.
"""

# The registry pages, the kinds that depend on the type, and a check's state live in
# enrichment_rules, which the facts layer reads without loading this worker. The worker's
# tests read REGISTRY_TYPE_KINDS and type_check_state here.
from .enrichment_rules import (  # noqa: F401
    ADSBDB,
    HEXDB,
    REGISTRY_TYPE_KINDS,
    SETTLED,
    TYPE_DEPENDENT_KINDS,
    type_check_state,
)
from .reference import clean
from .rules import IDENTIFIERS, identifier

NAME = "typecheck"
MAX_BYTES = 64 * 1024
RETENTION = 30 * 86400
# At most one lookup per second; the loop idles longer with nothing to check.
INTERVAL = 1
IDLE = 30
# Events older than this are no longer checked; the publisher works on recent encounters.
LOOKBACK = 7 * 86400
# An idle pass reads only events updated since the previous idle pass, less this
# overlap: the collector can write a report time a little older than its commit.
SCANNED = "typecheck_scanned_until"
SCAN_OVERLAP = 3600
# The most pending events one pass reads, and the most of one address it updates.
PENDING_LIMIT = 500
COLUMNS = ("hex", "source", "type_code", "registration", "fetched_at")


def _code(value):
    return identifier(value, IDENTIFIERS["type_code"])


def _adsbdb(payload):
    """adsbdb: response.aircraft.{icao_type, type, registration}; icao_type is the ICAO code."""
    response = payload.get("response")
    aircraft = response.get("aircraft") if isinstance(response, dict) else None
    if not isinstance(aircraft, dict):
        return None
    return {
        "type_code": _code(aircraft.get("icao_type")),
        "registration": identifier(aircraft.get("registration"), IDENTIFIERS["registration"]),
    }


def _hexdb(payload):
    """hexdb: ICAOTypeCode, Manufacturer, Type, Registration; empty fields when unknown."""
    if str(payload.get("status", "")) == "404":
        return None
    return {
        "type_code": _code(payload.get("ICAOTypeCode")),
        "registration": identifier(payload.get("Registration"), IDENTIFIERS["registration"]),
    }


# The registries in the order lookup() asks them: (name, base URL, response reader). Their
# names are enrichment_rules.REGISTRIES.
SOURCES = (("adsbdb", ADSBDB, _adsbdb), ("hexdb", HEXDB, _hexdb))


def agreement(listed, found):
    """Whether the second source's ICAO type code matches the listed one; None without one."""
    listed, found = _code(listed), _code(found)
    if listed is None or found is None:
        return None
    return listed == found


def _current(row, now):
    """A retained check, or None when it is missing or expired. now=None reads a frozen
    corpus, whose checks are kept as long as the corpus; the journal's expire after RETENTION."""
    if row is None or (now is not None and not 0 <= now - row["fetched_at"] <= RETENTION):
        return None
    return row


def check_evidence(aircraft, row, now):
    """The evidence block attach() writes: the source's answer, the listed type it
    was compared with and the resulting state."""
    listed = clean((aircraft or {}).get("t"))
    agrees = agreement(listed, row.get("type_code"))
    return {
        "state": "unavailable" if agrees is None else "agrees" if agrees else "disagrees",
        "source": row.get("source"),
        "type_code": row.get("type_code"),
        "registration": row.get("registration"),
        # The type the answer was compared with; a changed listing is checked again.
        "listed_type": listed,
        "checked_at": now,
    }


def failure_evidence(now, status=None):
    """The block written when no registry could be asked; the worker retries after its backoff."""
    return {
        "state": "failed",
        "source": None,
        "type_code": None,
        "registration": None,
        "listed_type": None,
        "checked_at": now,
        "http_status": status,
    }
