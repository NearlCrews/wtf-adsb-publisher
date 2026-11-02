"""An imported evidence view built from test candidates, as the engine would export it.

The live project's selection tests saved candidates into a collector journal and called
autopilot.collect(); this repository reads the engine's observations instead. inbox() groups
the candidates' findings into encounters the way the engine's selection.encounters does for an
otherwise empty journal (no learned profiles, no reviews, no identity cooldown), assesses each
finding with this repository's selection.assess, and gives each observation the episode key the
journal path would have used as its event id, so stories keep the keys the live tests expect.
"""

from copy import deepcopy

from wtf_adsb_publisher import curation, selection
from wtf_adsb_publisher.numeric import number

# The engine starts a new encounter after this many quiet seconds (selection.ENCOUNTER_GAP);
# the publisher has no encounter grouping of its own, so the value is copied here.
ENCOUNTER_GAP = 600
STREAM = "00000000-0000-4000-8000-000000000001"


def _assess(event, *, independent=False):
    return selection.assess(event, {}, independent=independent)


def encounters(events):
    """selection.encounters(identity_cooldown=False) over these findings, oldest first."""
    groups, latest, started = [], {}, {}
    # Oldest first, then by numeric id, as the journal's rows came.
    for event in sorted(events, key=lambda item: (item["first_seen"], int(item["id"]))):
        if curation.absorbed(event):
            continue
        event["selection"] = _assess(event)
        aircraft = event["evidence"]["aircraft"]
        start = number(event["evidence"].get("encounter_start"))
        registration = aircraft.get("r")
        group = started.get((event["hex"], start)) if start is not None else None
        if (
            group is not None
            and registration
            and group["registration"]
            and registration != group["registration"]
        ):
            group = None
        if group is None:
            group = latest.get(event["hex"])
            if (
                group is None
                or (
                    event["first_seen"] > group["last_seen"] + ENCOUNTER_GAP
                    and not (start is not None and start == group["start"])
                )
                or (start is not None and group["start"] is not None and start != group["start"])
                or (registration and group["registration"] and registration != group["registration"])
            ):
                group = {
                    "events": [],
                    "first_seen": event["first_seen"],
                    "last_seen": event["last_seen"],
                    "start": None,
                    "registration": registration,
                }
                groups.append(group)
        latest[event["hex"]] = group
        group["events"].append(event)
        group["last_seen"] = max(group["last_seen"], event["last_seen"])
        group["registration"] = registration or group["registration"]
        if group["start"] is None and start is not None and (event["hex"], start) not in started:
            group["start"] = start
            started[(event["hex"], start)] = group
    keys = set()
    for group in groups:
        if any(
            event["selection"]["promoted"] and not curation.context_only(event["kind"], event["evidence"])
            for event in group["events"]
        ):
            for event in group["events"]:
                if curation.context_only(event["kind"], event["evidence"]):
                    event["selection"] = _assess(event, independent=True)
        key = curation.episode_key(group)
        if key in keys:
            group["id"] = key = f"{key}#{group['events'][0]['id']}"
        keys.add(key)
    return groups


def empty_novelty(day):
    """The type-day counts of a journal with no coverage, as selection.novelty gave them."""
    return curation.type_day_counts({"coverage": [], "types": {}}, day)


def inbox(*items, now=None, novelty=empty_novelty, norms=None, stream=STREAM):
    """The publisher's imported state for these candidates.

    `now` leaves out findings first seen after it, as the journal read did; `novelty` is the
    type-day counts for a UTC day (curation.type_day_counts), by default an empty journal's; `norms` maps a finding id
    to the type norm the engine exported for it (context.type_norms, contract 1.1.0).
    """
    events = []
    for item in items:
        source = deepcopy(item["source"])
        if now is not None and source["first_seen"] > now:
            continue
        source.setdefault("category", "airframes")
        source.setdefault("title", "Synthetic observation")
        source.setdefault("origin", "Synthetic fixture")
        source.setdefault("verdict", None)
        events.append(source)
    observations = []
    for group in encounters(events):
        key = curation.episode_key(group)
        context = {"novelty": novelty(curation.utc_day(group["first_seen"])) if novelty else None}
        exported = {
            event["id"]: norms[event["id"]] for event in group["events"] if event["id"] in (norms or {})
        }
        if exported:
            context["type_norms"] = exported
        observations.append(
            {
                "event_id": key,
                "revision": 1,
                "observation": {
                    "group": {**group, "id": key},
                    "context": context,
                    "media": {"by_finding": {}},
                },
            }
        )
    return {
        "stream_id": stream,
        "cursor": len(observations),
        "observations": observations,
        "retractions": [],
        "health": {"ready": True, "complete": True, "mode": "replay"},
        "synthetic": False,
    }
