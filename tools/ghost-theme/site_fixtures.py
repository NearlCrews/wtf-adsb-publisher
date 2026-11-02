#!/usr/bin/env python3
"""Synthetic public snapshots for the site build checks.

Every value is invented test data built through the real ledger, projection and
snapshot validators: no network, collector database, ledger, or private file is
read. Most events carry the story engine's ``story``, ``context``,
``released_at`` and ``content_revised_at`` fields. One event, the circling
events and the weekly fixture leave those fields out, as snapshots made before
the story engine did; every event still has a ``/stories/{slug}/`` path, the
only article path the exporter accepts. The two certified-limit stories are
written by the story engine itself from detector-shaped findings, so the build
renders exactly what the publisher would. A published snapshot also lists two
withdrawn stories' paths, which the build turns into redirect pages.

    python3 tools/ghost-theme/site_fixtures.py --mode published > snapshot.json
"""

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(TOOLS))
from import_snapshot import load  # noqa: E402

from wtf_adsb_publisher import public_events as public  # noqa: E402
from wtf_adsb_publisher import (
    publication,  # noqa: E402
    stories,  # noqa: E402
)

AVAILABLE_FROM = "2026-09-01"
AIRBUS = {
    "id": "src.A388.1",
    "publisher": "Airbus",
    "title": "A380 aircraft overview",
    "url": "https://www.airbus.com/en/products-services/commercial-aircraft/passenger-aircraft/a380",
}
BOEING = {
    "id": "src.DC3.1",
    "publisher": "Boeing",
    "title": "DC-3 commercial transport history",
    "url": "https://www.boeing.com/history/products/dc-3",
}
A380 = {"code": "A388", "name": "Airbus A380-800", "slug": "airbus-a380"}
ASIANA = {
    "name": "Asiana Airlines",
    "slug": "asiana-airlines",
    "basis": "callsign_designator",
    "designator": "AAR",
}
A380_FACT = "Airbus built the A380 with two full-length passenger decks."


def story(headline, dek, paragraphs, *, seo_title, angle="snapshot", facts=None, sources=()):
    body = {"headline": headline, "dek": dek, "paragraphs": paragraphs}
    digest = hashlib.sha256(json.dumps({**body, "facts": facts or {}}, sort_keys=True).encode()).hexdigest()
    return {
        "version": 1,
        "source": "template",
        "angle": angle,
        "variant": "h1/d1/o1",
        "headline": headline,
        "seo_title": seo_title,
        "dek": dek,
        "paragraphs": paragraphs,
        "opener_skeleton": "the aircraft database lists reg as",
        "headline_skeleton": "op type reg near place",
        "facts_digest": digest,
        "facts": facts or {},
        "sources": list(sources),
    }


def context(topic, section, kind=None, registration=None, operator=None, place=None, country=None):
    return {
        "topic": topic,
        "section": section,
        "type": kind,
        "registration_slug": registration,
        "operator": operator,
        "place": place,
        "country": country,
    }


SPECS = [
    {
        "key": "fixture-hl7635-0927",
        "kind": "watch_type",
        "start": "2026-09-27T00:05:15Z",
        "minutes": 17,
        "aircraft": {
            "hex": "71c635",
            "r": "HL7635",
            "t": "A388",
            "desc": "AIRBUS A-380-800",
            "flight": "AAR224",
        },
        "slug": "airbus-a380-hl7635-27-sep-2026",
        "story": story(
            "Asiana A380 HL7635 passes near Coleman, Michigan",
            "Flying as AAR224, the double-deck Airbus crossed high and fast just after midnight UTC.",
            [
                "HL7635 broadcast callsign AAR224 just after midnight UTC on 27 September. The aircraft database "
                "lists the registration as an Airbus A380-800, and the AAR prefix of that callsign is assigned "
                "to Asiana Airlines. Its saved positions near Coleman trace a short stretch of a high, fast crossing.",
                A380_FACT + " The record below lists every saved reading and what the data cannot show.",
            ],
            seo_title="Asiana A380 HL7635 near Coleman, MI",
            angle="operator_snapshot",
            facts={"tp.A388.1": A380_FACT},
            sources=[AIRBUS],
        ),
        "context": context(
            "notable-aircraft", "unusual-aircraft", A380, "hl7635", ASIANA, "Coleman, Michigan", "South Korea"
        ),
        "released_minutes": 40,
        # Its prose was revised more than a day after release, so the page says "Updated".
        "content_revised_hours": 30,
    },
    {
        "key": "fixture-hl7635-0916",
        "kind": "watch_type",
        "start": "2026-09-16T04:00:00Z",
        "minutes": 12,
        "aircraft": {
            "hex": "71c635",
            "r": "HL7635",
            "t": "A388",
            "desc": "AIRBUS A-380-800",
            "flight": "AAR226",
        },
        "slug": "airbus-a380-hl7635-16-sep-2026",
        "story": story(
            "Asiana A380 HL7635 passes high over Battle Creek, Michigan",
            "Asiana's double-deck Airbus flew as AAR226 at cruise height early in the UTC day.",
            [
                "Asiana Airlines' A380 HL7635 crossed the region early on 16 September UTC, flying as AAR226. The "
                "aircraft database lists the registration as an Airbus A380-800, and the AAR prefix ties the "
                "callsign to the airline.",
                A380_FACT
                + " The saved track covers only a short part of a much longer flight over Battle Creek.",
            ],
            seo_title="Asiana A380 HL7635 near Battle Creek, MI",
            angle="operator_snapshot",
            facts={"tp.A388.1": A380_FACT},
            sources=[AIRBUS],
        ),
        "context": context(
            "notable-aircraft",
            "unusual-aircraft",
            A380,
            "hl7635",
            ASIANA,
            "Battle Creek, Michigan",
            "South Korea",
        ),
        "released_minutes": 25,
    },
    {
        "key": "fixture-hl7640-0920",
        "kind": "watch_type",
        "start": "2026-09-20T03:30:00Z",
        "minutes": 9,
        "aircraft": {
            "hex": "71c640",
            "r": "HL7640",
            "t": "A388",
            "desc": "AIRBUS A-380-800",
            "flight": "AAR206",
        },
        "slug": "airbus-a380-hl7640-20-sep-2026",
        "story": story(
            "A second Asiana A380, HL7640, crosses Michigan as AAR206",
            "Another of Asiana's double-deck jets reported its position over the state for nine minutes.",
            [
                "HL7640 is the second Asiana A380 in these stories, seen four days after HL7635 first appeared. "
                "It flew as AAR206, and the aircraft database lists the registration as an Airbus A380-800.",
                A380_FACT
                + " Nine minutes of saved ADS-B positions cover its pass over the state, and the record lists "
                "the speed and altitude readings that came with them, along with the limits of a database match.",
            ],
            seo_title="Asiana A380 HL7640 over Michigan",
            angle="operator_snapshot",
            facts={"tp.A388.1": A380_FACT},
            sources=[AIRBUS],
        ),
        "context": context(
            "notable-aircraft", "unusual-aircraft", A380, "hl7640", ASIANA, None, "South Korea"
        ),
        "released_minutes": 30,
    },
    {
        "key": "fixture-gxleb-1004",
        "kind": "watch_type",
        "start": "2026-10-04T04:00:00Z",
        "minutes": 8,
        "aircraft": {
            "hex": "4007f5",
            "r": "G-XLEB",
            "t": "A388",
            "desc": "AIRBUS A-380-800",
            "flight": "BAW8K",
        },
        "slug": "airbus-a380-g-xleb-4-oct-2026",
        "story": story(
            "British Airways A380 G-XLEB heads across Michigan as BAW8K",
            "A British-registered A380 joins the Asiana jets in the journal's list of double-deck sightings.",
            ["G-XLEB broadcast callsign BAW8K over Michigan.", A380_FACT],
            seo_title="British Airways A380 G-XLEB over Michigan",
            facts={"tp.A388.1": A380_FACT},
            sources=[AIRBUS],
        ),
        "context": context(
            "notable-aircraft",
            "unusual-aircraft",
            A380,
            "g-xleb",
            {
                "name": "British Airways",
                "slug": "british-airways",
                "basis": "callsign_designator",
                "designator": "BAW",
            },
            None,
            "United Kingdom",
        ),
        "released_minutes": 35,
    },
    {
        "key": "fixture-n8704-1002",
        "kind": "vintage",
        "start": "2026-10-02T12:51:00Z",
        "minutes": 4,
        "aircraft": {"hex": "abc8a4", "r": "N8704", "t": "DC3", "desc": "DOUGLAS DC-3"},
        "source": "mlat",
        "slug": "douglas-dc-3-n8704-2-oct-2026",
        "story": story(
            "Douglas DC-3 N8704, listed as a 1944 build, flies over Fenton, Michigan",
            "The database record makes this Dakota one of the oldest aircraft in these stories.",
            [
                "N8704 is a Douglas DC-3 by its database record, built in 1944. The archive saved a short "
                "stretch of its flight over Fenton, Michigan, with the readings listed below.",
                "The DC-3 first flew in December 1935, so by its database year this airframe came off the "
                "line nine years into the type's life. Old aircraft records can be incomplete, and the "
                "registration may have moved between airframes.",
            ],
            seo_title="Douglas DC-3 N8704 near Fenton, MI",
            angle="age",
            facts={"tp.DC3.1": "The DC-3 first flew on 17 December 1935.", "age.built": 1944},
            sources=[BOEING],
        ),
        "context": context(
            "vintage-aircraft",
            "unusual-aircraft",
            {"code": "DC3", "name": "Douglas DC-3", "slug": "douglas-dc-3"},
            "n8704",
            None,
            "Fenton, Michigan",
            "United States",
        ),
        "released_minutes": 45,
        "revised_hours": 45,
    },
    {
        "key": "fixture-n6508k-0918",
        "kind": "outlier_gs",
        "start": "2026-09-18T16:14:00Z",
        "minutes": 6,
        "aircraft": {"hex": "a8b1c2", "r": "N6508K", "t": "C172", "desc": "CESSNA 172 Skyhawk"},
        "slug": "cessna-172-n6508k-ground-speed-spike-18-sep-2026",
        "story": story(
            "Cessna 172 N6508K logs one ground-speed report far above its neighbors",
            "Every other saved ground-speed report around it stayed near a light aircraft's normal pace.",
            [
                "N6508K, a Cessna 172 by its database record, was flying at its usual pace when the archive "
                "saved one ground-speed report far outside the reports before and after it.",
                "Every other reading in that stretch stayed close to the ones before and after it, and the "
                "one report stands alone. The chart below plots each saved report so the spike can be "
                "compared with the rest.",
            ],
            seo_title="Cessna 172 N6508K: one ground-speed spike",
            angle="jump_and_return",
        ),
        "context": context(
            "ads-b-glitches",
            "data-quality",
            {"code": "C172", "name": "Cessna 172", "slug": "cessna-172"},
            "n6508k",
            None,
            None,
            "United States",
        ),
        "released_minutes": 50,
        # A revision within a day of release moves dateModified but shows no label.
        "content_revised_hours": 3,
    },
    {
        "key": "fixture-hbal837-1002",
        "kind": "stratospheric_balloon",
        "start": "2026-10-02T22:07:00Z",
        "minutes": 300,
        "aircraft": {"hex": "acf0a1", "r": "N930TH", "flight": "HBAL837"},
        "slug": "hbal837-high-altitude-balloon-2-oct-2026",
        "story": story(
            "HBAL837 drifts slowly at extreme altitude, like a high-altitude balloon",
            "The flight registered as N930TH moved slowly over the ground for hours, far above airliner traffic.",
            [
                "For several hours the aircraft registered as N930TH reported very high pressure altitudes while "
                "moving slowly over the ground. That slow drift at extreme altitude is consistent with a "
                "high-altitude balloon.",
                "Its callsign, HBAL837, carries a prefix used for balloon flights. Who launched the flight and "
                "why are not in the data, and the saved positions show only the part this archive recorded.",
            ],
            seo_title="HBAL837 high-altitude balloon drift",
            angle="drift",
        ),
        "context": context("high-altitude-balloons", "unusual-aircraft", None, "n930th", None, None, None),
        "released_minutes": 60,
    },
    {
        "key": "fixture-26903-0908",
        "kind": "first_type_here",
        "start": "2026-09-08T14:00:00Z",
        "minutes": 11,
        "aircraft": {"hex": "497cde", "r": "26903", "t": "E390", "desc": "EMBRAER EMB-390"},
        "slug": "embraer-c-390-26903-first-sighting-8-sep-2026",
        "story": story(
            "Embraer C-390 spotted for the first time",
            "The database lists 26903 as an Embraer military transport. Its address sits in Portugal's block.",
            [
                "An Embraer C-390, serial 26903, appeared in these records for the first time on 8 September. "
                "Its saved positions are plotted below, starting over the southern part of the state.",
                "This is a first for these records only. It says nothing about how often the type flies "
                "elsewhere, and the database match does not show the aircraft's task that day or who flew it.",
            ],
            seo_title="Embraer C-390 first sighting: 26903, 8 Sep 2026",
            angle="first_in_records",
        ),
        "context": context(
            "first-sightings",
            "unusual-aircraft",
            {"code": "E390", "name": "Embraer C-390 Millennium", "slug": "embraer-c-390"},
            "26903",
            None,
            None,
            "Portugal",
        ),
        "released_minutes": 55,
    },
    {
        "key": "fixture-n3730b-0925",
        "kind": "special_squawk_7700",
        "start": "2026-09-25T18:20:00Z",
        "minutes": 7,
        "aircraft": {
            "hex": "a4c3d2",
            "r": "N3730B",
            "t": "B738",
            "desc": "BOEING 737-800",
            "flight": "DAL2321",
        },
        "slug": "boeing-737-800-n3730b-squawk-7700-25-sep-2026",
        "story": story(
            "Delta Boeing 737-800 N3730B transmits squawk 7700 over Michigan",
            "The general emergency code appeared in its reports; the code alone does not confirm an emergency.",
            ["N3730B transmitted the general emergency code while flying as DAL2321."],
            seo_title="Delta 737-800 N3730B: squawk 7700",
            angle="code_meaning",
        ),
        "context": context(
            "squawk-codes",
            "transponder-reports",
            {"code": "B738", "name": "Boeing 737-800", "slug": "boeing-737-800"},
            "n3730b",
            {
                "name": "Delta Air Lines",
                "slug": "delta-air-lines",
                "basis": "callsign_designator",
                "designator": "DAL",
            },
            None,
            "United States",
        ),
        "released_minutes": 20,
    },
    {
        "key": "fixture-tcas-1004",
        "kind": "tcas_ra",
        "start": "2026-10-04T11:25:00Z",
        "minutes": 3,
        "aircraft": {"hex": "a6e51b", "r": "N51EM", "t": "C25C", "desc": "CESSNA Citation CJ4"},
        "slug": "citation-cj4-boeing-767-tcas-advisory-4-oct-2026",
        "story": story(
            "TCAS tells a climbing Citation CJ4 to level off below a Boeing 767",
            "The jet's climb slowed within seconds of the advisory; the broadcast does not establish a near miss.",
            [
                "A Cessna Citation CJ4, by its database record, broadcast a TCAS resolution advisory telling it "
                "to level off while it climbed. The advisory named a Boeing 767 flying level above it.",
                "TCAS issues these instructions automatically, and level-off advisories often follow a fast "
                "climb toward another aircraft's altitude. The broadcast does not establish a near miss or a "
                "loss of separation.",
            ],
            seo_title="TCAS advisory: Citation CJ4 and Boeing 767",
            angle="advisory_response",
        ),
        "context": context(
            "tcas-advisories",
            "transponder-reports",
            {"code": "C25C", "name": "Cessna Citation CJ4", "slug": "cessna-citation-cj4"},
            "n51em",
            None,
            None,
            "United States",
        ),
        "released_minutes": 15,
    },
    {
        # A story-less event: no story, context, or release fields, under a /stories/ path.
        "key": "fixture-870043-1004",
        "kind": "watch_type",
        "start": "2026-10-04T03:49:00Z",
        "minutes": 1,
        "aircraft": {"hex": "ae0f3b", "r": "87-0043", "t": "C5M", "desc": "Lockheed C-5M Super Galaxy"},
        "slug": "c-5m-super-galaxy-87-0043-4-oct-2026",
        "title": "87-0043: C5M type match",
        "summary": (
            "The saved aircraft database identifies this aircraft as Lockheed C-5M Super Galaxy. The type match "
            "does not independently verify its historical assignment or the purpose of this flight."
        ),
    },
]


# Readings past a certified limit: the collector's comparison, a second registry agreeing
# with the type, and the story engine's own template text and routing (rated_story()). Their
# types appear nowhere else in the fixture, so no type hub crosses its threshold on their account.
SPECS += [
    {
        "key": "fixture-n295rl-0924",
        "kind": "above_rated_altitude",
        "rated": True,
        "start": "2026-09-24T19:10:00Z",
        "aircraft": {"hex": "a9c8b1", "r": "N295RL", "t": "E295", "desc": "EMBRAER E195-E2"},
        "slug": "embraer-e195-e2-n295rl-above-certified-ceiling-24-sep-2026",
        "released_minutes": 35,
    },
    {
        "key": "fixture-n320rs-0925",
        "kind": "above_rated_speed",
        "rated": True,
        "start": "2026-09-25T21:30:00Z",
        "aircraft": {"hex": "a9c8b2", "r": "N320RS", "t": "A320", "desc": "AIRBUS A-320"},
        "slug": "airbus-a320-n320rs-above-maximum-speed-25-sep-2026",
        "released_minutes": 35,
    },
]


# Withdrawn stories' old paths, as a published export lists them: one whose type
# still has a hub (the Cessna 172 story above builds it) and one whose type has
# none, so its page points to the journal.
RETIRED = [
    {
        "path": "/stories/cessna-172-n4172q-ground-speed-spike-12-sep-2026/",
        "type": "cessna-172",
        "hours_before": 20,
    },
    {
        "path": "/stories/piper-pa-28-n2817p-above-maximum-speed-14-sep-2026/",
        "type": "piper-pa-28",
        "hours_before": 6,
    },
]
# Where each withdrawn path's page sends readers, and the link text it shows.
RETIRED_HUBS = {
    RETIRED[0]["path"]: ("/aircraft/cessna-172/", "other Cessna 172 stories"),
    RETIRED[1]["path"]: ("/stories/", "the story journal"),
}


# Story-less circling events (also under /stories/ paths) on separate days fill a
# second archive page. The last is the newest event of all, so the home page
# has a tile whose type (the R44) has no photo and shows its class photo.
EXTRA_DAYS = [(6 + index, "sep", "09") for index in range(14)] + [(5, "oct", "10")]
EXTRA_COUNT = len(EXTRA_DAYS)


def extra_specs(count=EXTRA_COUNT):
    """The circling events; past one a day, more on the same days, each round an hour earlier.

    A larger count pages the archive and the circling events' topic; the newest event stays the
    same. Ten rounds fit in the day, so at most ten times EXTRA_COUNT.
    """
    if not 0 <= count <= 10 * len(EXTRA_DAYS):
        raise ValueError(f"The extra count must be between 0 and {10 * len(EXTRA_DAYS)}")
    specs = []
    for index in range(count):
        day, name, month = EXTRA_DAYS[index % len(EXTRA_DAYS)]
        hour = (15 if month == "09" else 9) - index // len(EXTRA_DAYS)
        specs.append(
            {
                "key": f"fixture-extra-{index:02d}",
                "kind": "circling",
                "start": f"2026-{month}-{day:02d}T{hour:02d}:00:00Z",
                "minutes": 10,
                "aircraft": {
                    "hex": f"a0{index:04x}",
                    "r": f"N{20 + index}FX",
                    "t": "R44",
                    "desc": "ROBINSON R-44",
                },
                "slug": f"robinson-r44-n{20 + index}fx-circling-{day}-{name}-2026",
                "title": f"N{20 + index}FX: circling",
                "summary": "A synthetic helicopter fixture without story fields.",
            }
        )
    return specs


EXTRA = extra_specs()


def utc(value):
    return public.utc(value.isoformat())


stamp = public._instant


def finding(spec):
    from test_weekly_spotlight import finding as base

    if spec.get("rated"):
        from story_fixtures import rated_event

        aircraft = spec["aircraft"]
        common = {"hexcode": aircraft["hex"], "registration": aircraft["r"], "callsign": None}
        return rated_event(
            spec["kind"],
            stamp(spec["start"]).timestamp(),
            common,
            typecode=aircraft["t"],
            desc=aircraft["desc"],
        )
    start = stamp(spec["start"])
    end = start + timedelta(minutes=spec["minutes"])
    source = base(utc(start), utc(end))
    source["kind"] = spec["kind"]
    source["hex"] = spec["aircraft"]["hex"]
    # The readings' reception source (the public event's `source`); ADS-B
    # unless the spec names another, such as MLAT for the DC-3.
    source["evidence"]["aircraft"] = {
        key: value
        for key, value in {**spec["aircraft"], "type": spec.get("source", "adsb_icao"), "gs": 120}.items()
        if value
    }
    return source


def packet(spec):
    """The evidence packet the publisher saves for the spec's finding, without its track."""
    source = finding(spec)
    return stories.evidence_packet(
        source, include_track=False, observation_interval=(source["first_seen"], source["last_seen"])
    )


def put(ledger, spec, metadata, at):
    """Saves the spec's finding to the ledger as the publisher does; returns its public id."""
    return public.put_event(ledger, spec["key"], packet(spec), metadata, at=at)


def rated_story(spec):
    """The spec with the story engine's text and routing for its finding, as the publisher writes them."""
    from wtf_adsb_publisher import editorial as engine
    from wtf_adsb_publisher import story_facts

    if not spec.get("rated"):
        return spec
    evidence = packet(spec)
    facts = story_facts.build(evidence, public._projection(evidence, public.PLACEHOLDER_EDITORIAL))
    story, report = engine.render_with_report(facts, spec["key"])
    if not report.publishable or report.fallback:
        raise ValueError(f"The story engine could not write {spec['key']}: {report.issues}")
    return {**spec, "story": story, "context": facts["context"], "facts": facts}


def editorial(spec):
    path = f"/stories/{spec['slug']}/"
    content = spec.get("story")
    return {
        "title": content["headline"] if content else spec["title"],
        "summary": content["dek"] if content else spec["summary"],
        "article_path": path,
        "ghost_id": hashlib.sha256(path.encode()).hexdigest()[:24],
        "secondary_labels": [],
    }


def site_snapshot(mode="published", as_of=None, *, extra=True, extra_count=EXTRA_COUNT):
    """A published or review snapshot with story, context, and legacy events."""
    from test_weekly_spotlight import NOW
    from test_weekly_spotlight import fixture as weekly_fixture

    as_of = as_of or NOW
    ledger, _, _, _ = weekly_fixture(as_of=as_of)
    ledger["available_from"] = AVAILABLE_FROM
    prepared = [rated_story(spec) for spec in SPECS]
    extras = extra_specs(extra_count) if extra else []
    for spec in [*prepared, *extras]:
        identifier = put(ledger, spec, editorial(spec), as_of)
        public.set_review_state(ledger, identifier, "approved", at=as_of)
    snapshot = public.export_snapshot(ledger, mode="review", as_of=as_of)
    specs = {f"/stories/{spec['slug']}/": spec for spec in [*prepared, *extras]}
    for event in snapshot["events"]:
        spec = specs.get(event["article_path"])
        end = stamp(event["observed_end"])
        released = end + timedelta(minutes=(spec or {}).get("released_minutes", 30))
        # A prose or evidence revision is a Ghost write too; a card refresh
        # (revised_hours) moves only Ghost's updated_at.
        content = released + timedelta(hours=(spec or {}).get("content_revised_hours", 0))
        if mode == "published":
            event["publication_status"] = "published"
            event["published_at"] = publication.ghost_post_date(event["observed_start"])
            revised = max(content, released + timedelta(hours=(spec or {}).get("revised_hours", 0)))
            event["revised_at"] = utc(min(revised, stamp(as_of)))
        if spec and "story" in spec:
            event["story"] = deepcopy(spec["story"])
            event["context"] = deepcopy(spec["context"])
            if mode == "published":
                event["released_at"] = utc(min(released, stamp(as_of)))
                event["content_revised_at"] = utc(min(content, stamp(as_of)))
    if mode == "published":
        snapshot["mode"] = snapshot["publication_status"] = "published"
        snapshot["retired"] = [
            {
                "path": entry["path"],
                "type": entry["type"],
                "withdrawn_at": utc(stamp(as_of) - timedelta(hours=entry["hours_before"])),
            }
            for entry in RETIRED
        ]
    snapshot["search"] = public._search(snapshot["events"])
    snapshot.pop("snapshot_id")
    snapshot["snapshot_id"] = public._digest(snapshot)
    return load(snapshot)["snapshot"]


# Synthetic figures: tracks near pinned towns, one speed chart, summary cards.
MEDIA = {
    "/stories/airbus-a380-hl7635-27-sep-2026/": ("track", "Coleman"),
    "/stories/douglas-dc-3-n8704-2-oct-2026/": ("track", "Fenton"),
    "/stories/hbal837-high-altitude-balloon-2-oct-2026/": ("track", "Midland"),
    "/stories/cessna-172-n6508k-ground-speed-spike-18-sep-2026/": ("chart", None),
}


def site_media(snapshot, destination):
    """Write a media manifest and its assets for every event, as the publisher does."""
    from wtf_adsb_publisher.places import dataset

    destination = Path(destination).resolve()
    if not destination.is_relative_to((ROOT / "artifacts/ghost-theme").resolve()):
        raise ValueError("Fixture media must be written under artifacts/ghost-theme")
    from run_autopilot import media_module

    module = media_module()
    towns = {
        place["name"]: place["point"]
        for place in dataset()["places"]
        if place["kind"] == "city" and place.get("region") == "Michigan"
    }
    assets = destination / "event-media"
    manifest = {"schema_version": 1, "snapshot_id": snapshot["snapshot_id"], "events": {}}
    for event in snapshot["events"]:
        plan = MEDIA.get(event["article_path"])
        start, end = module.instant(event["observed_start"]), module.instant(event["observed_end"])
        image = None
        if plan and plan[0] == "track":
            lon, lat = towns[plan[1]]
            step = min(20, (end - start) / 11)
            points = [
                {
                    "ts": start + index * step,
                    "lat": lat - 0.03 + index * 0.006,
                    "lon": lon - 0.04 + index * 0.008,
                    "source": "adsb_icao",
                    "gs": 440 + index,
                    "alt_baro": 37000,
                }
                for index in range(12)
            ]
            image = module.track_image(event, module.segments(points, start, end), analysis_window=False)
        elif plan:
            peak = start + (end - start) / 2
            samples = [
                {"timestamp": peak + offset, "gs": value, "source": "adsb_icao"}
                for offset, value in ((-30, 110.8), (-15, 112.0), (0, 1063.9), (15, 118.1), (30, 116.4))
            ]
            finding = {"value": 1063.9, "evidence": {"observed_at": peak, "context_samples": samples}}
            image = module.speed_image(event, finding)
        manifest["events"][event["id"]] = module.store_image(
            image or module.summary_image(event), assets, event
        )
    path = destination / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return path


def legacy_snapshot():
    """The weekly fixture alone, exactly as older exports shaped it."""
    from test_weekly_spotlight import fixture as weekly_fixture

    return weekly_fixture()[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("published", "review"), default="published")
    parser.add_argument("--legacy", action="store_true", help="Only the older, story-less weekly fixture")
    parser.add_argument(
        "--media", type=Path, help="Also write a media manifest here (under artifacts/ghost-theme)"
    )
    parser.add_argument("--as-of", help="The snapshot's as_of_utc time, such as 2026-10-12T15:00:00Z")
    parser.add_argument(
        "--no-extra", action="store_true", help="Leave out the extra stories (the main specs only)"
    )
    parser.add_argument(
        "--extra-count",
        type=int,
        default=EXTRA_COUNT,
        help=f"How many extra circling stories (default {EXTRA_COUNT}; more page the archive and their topic)",
    )
    args = parser.parse_args()
    value = (
        legacy_snapshot()
        if args.legacy
        else site_snapshot(args.mode, args.as_of, extra=not args.no_extra, extra_count=args.extra_count)
    )
    if args.media:
        site_media(value, args.media)
    json.dump(value, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
