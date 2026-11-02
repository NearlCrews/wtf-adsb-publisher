"""The day quota decides publication slots as publish applied them inline."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import curation

DAY = "2026-10-01"
EARLIER = "2026-09-30"
NOON = 1790856000.0  # 2026-10-01T12:00:00Z


def live_slot(day=DAY, address=None, exceptional=False):
    return curation.LiveSlot(day, frozenset({address} if address else set()), exceptional)


def claim(
    identifier, day=DAY, *, address=None, live=None, standing=False, sticky=None, rank=None, start=NOON
):
    return curation.Claim(
        identifier,
        curation.Slot(day, frozenset({address} if address else set())),
        live,
        standing,
        standing if sticky is None else sticky,
        rank,
        start,
    )


def seeded(identifier, day=DAY, *, address=None, exceptional=False, **kwargs):
    """A live story still on the date it went live on."""
    slot = live_slot(day, address, exceptional)
    kwargs.setdefault("standing", exceptional)
    return claim(identifier, day, address=address, live=slot, **kwargs)


def ordinary_day(count, day=DAY):
    return [seeded(f"o{i}", day, address=f"o{i}") for i in range(count)]


def day_quota(live):
    """A quota seeded from these live claims' slots, in order."""
    return curation.DayQuota({item.id: item.live for item in live})


def decision(quota, item):
    return tuple(quota.decide(item))


def tally(quota):
    """The quota's nonzero totals and ordinary counts per date, and its held exceptional slots."""
    days = sorted(day for day in {*quota.counts, *quota.ordinary} if quota.counts[day] or quota.ordinary[day])
    return {
        "per_day": {day: {"total": quota.counts[day], "ordinary": quota.ordinary[day]} for day in days},
        "exceptions": {held: count for held, count in quota.exceptions.items() if count},
    }


class DayQuotaDecisions(unittest.TestCase):
    def test_a_tenth_ordinary_story_on_a_day_is_refused_for_the_daily_limit(self):
        quota = day_quota(ordinary_day(9))
        self.assertEqual(decision(quota, claim("new", address="n")), (False, False, "daily_limit"))
        self.assertEqual(decision(quota, claim("other", EARLIER, address="n")), (True, False, None))

    def test_an_exceptional_story_is_admitted_over_the_limit_with_priority(self):
        quota = day_quota(ordinary_day(9))
        self.assertEqual(decision(quota, claim("new", address="e", standing=True)), (True, True, None))

    def test_a_third_exception_for_one_aircraft_and_day_is_demoted_not_refused(self):
        live = [seeded(f"x{i}", address="abcdef", exceptional=True) for i in range(2)]
        quota = day_quota(live)
        third = claim("third", address="abcdef", standing=True)
        self.assertEqual(decision(quota, third), (True, False, "aircraft_cap"))
        # Demoted, it competes for an ordinary slot and can lose it.
        full = day_quota(live + ordinary_day(7))
        self.assertEqual(decision(full, third), (False, False, "daily_limit"))
        # The cap is per address and date.
        self.assertEqual(
            decision(quota, claim("t", EARLIER, address="abcdef", standing=True))[:2], (True, True)
        )
        with patch.object(curation, "EXCEPTIONAL_PER_AIRCRAFT", 3):
            self.assertEqual(decision(quota, third), (True, True, None))

    def test_a_live_story_checks_the_ordinary_count_and_a_new_one_the_total(self):
        live = ordinary_day(8) + [seeded("x", address="abcdef", exceptional=True)]
        quota = day_quota(live)
        self.assertEqual(tally(quota)["per_day"], {DAY: {"total": 9, "ordinary": 8}})
        # The live story already holds its place among the eight ordinary ones.
        self.assertEqual(decision(quota, live[0]), (True, False, None))
        # A new story checks the total, which the exception fills.
        self.assertEqual(decision(quota, claim("new", address="n")), (False, False, "daily_limit"))
        # A live exception that lost its standing joins the ordinary count, which has room.
        demoted = seeded("x", address="abcdef", exceptional=True, standing=False, sticky=False)
        self.assertEqual(decision(quota, demoted), (True, False, None))
        # With nine ordinary stories it has none.
        crowded = day_quota(ordinary_day(9) + [seeded("x", address="abcdef", exceptional=True)])
        self.assertEqual(decision(crowded, demoted), (False, False, "daily_limit"))
        self.assertEqual(decision(crowded, ordinary_day(9)[0]), (True, False, None))

    def test_a_granted_live_exception_keeps_its_sticky_standing(self):
        live = seeded("x", address="abcdef", exceptional=True, standing=False, sticky=True)
        quota = day_quota([live, *ordinary_day(9)])
        self.assertEqual(decision(quota, live), (True, True, None))
        # A story that holds no exceptional slot is judged without the sticky standing.
        new = claim("new", address="abcdef", standing=False, sticky=True)
        self.assertEqual(decision(quota, new), (False, False, "daily_limit"))

    def test_a_live_exception_over_the_cap_is_displaced_and_counts_as_neither(self):
        burst = [seeded(f"x{i}", address="abcdef", exceptional=True) for i in range(3)]
        quota = day_quota(burst)
        self.assertEqual(quota.granted, {"x0", "x1"})
        self.assertEqual(quota.displaced, {"x2"})
        self.assertEqual(
            tally(quota),
            {"per_day": {DAY: {"total": 3, "ordinary": 0}}, "exceptions": {("abcdef", DAY): 2}},
        )
        self.assertEqual(decision(quota, burst[2]), (True, False, "aircraft_cap"))

    def test_order_puts_exceptions_first_newest_then_live_then_unprojectable(self):
        quota = day_quota([seeded("live-ranked-3", rank=3), seeded("live-ranked-7", rank=7)])
        claims = [
            curation.Claim("unprojectable", None, None, False, False, None, None),
            claim("new-ranked-5", rank=5),
            claim("new-unranked", start=NOON + 60),
            claim("new-ranked-2", rank=2),
            seeded("live-ranked-3", rank=3),
            seeded("live-ranked-7", rank=7),
            claim("older-exception", EARLIER, standing=True, start=NOON - 86400),
            claim("exception", standing=True),
            claim("new-later-unranked", start=NOON + 120),
            claim("earlier-ordinary", EARLIER, start=NOON - 86400),
        ]
        self.assertEqual(
            [item.id for item in quota.order(claims)],
            [
                "exception",
                "older-exception",
                # An over-full date gives up its worst-ranked live story first,
                "live-ranked-7",
                "live-ranked-3",
                # and scarce new slots follow the curation rank, then the later start.
                "new-ranked-2",
                "new-ranked-5",
                "new-later-unranked",
                "new-unranked",
                "earlier-ordinary",
                "unprojectable",
            ],
        )

    def test_relocate_frees_the_previous_live_slot_not_the_current_one(self):
        moved = claim("x0", EARLIER, address="abcdef", live=live_slot(DAY, "abcdef", True), standing=True)
        quota = day_quota([seeded("x0", address="abcdef", exceptional=True)])
        made = quota.decide(moved)
        self.assertEqual(tuple(made), (True, True, None))
        self.assertEqual(quota.granted, set())
        self.assertEqual(quota.exceptions[("abcdef", DAY)], 0)
        # The date's total moves only when the story is written.
        self.assertEqual(quota.counts[DAY], 1)
        quota.commit(moved, made)
        self.assertEqual(
            tally(quota),
            {"per_day": {EARLIER: {"total": 1, "ordinary": 0}}, "exceptions": {("abcdef", EARLIER): 1}},
        )
        # A story still on its date keeps its exceptional slot.
        same = seeded("y", address="bbbbbb", exceptional=True)
        quota = day_quota([same])
        quota.decide(same)
        self.assertEqual(quota.granted, {"y"})

    def test_a_corrected_date_takes_its_own_place_under_the_cap(self):
        live = [seeded(f"x{i}", EARLIER, address="abcdef", exceptional=True) for i in range(2)]
        moved = claim("y", EARLIER, address="abcdef", live=live_slot(DAY, "abcdef", True), standing=True)
        quota = day_quota([*live, seeded("y", address="abcdef", exceptional=True)])
        self.assertEqual(decision(quota, moved), (True, False, "aircraft_cap"))

    def test_commit_moves_the_counts_to_the_written_slot(self):
        quota = day_quota(ordinary_day(2) + [seeded("x", address="abcdef", exceptional=True)])
        new = claim("new", address="n")
        quota.commit(new, quota.decide(new))
        exception = claim("e", address="e", standing=True)
        quota.commit(exception, quota.decide(exception))
        # A granted live exception released as ordinary gives back its exceptional slot.
        demoted = seeded("x", address="abcdef", exceptional=True, standing=False, sticky=False)
        quota.commit(demoted, quota.decide(demoted))
        # A live ordinary story whose date was corrected moves to the corrected date.
        corrected = claim("o0", EARLIER, address="o0", live=live_slot(DAY, "o0"))
        quota.commit(corrected, quota.decide(corrected))
        self.assertEqual(
            tally(quota),
            {
                "per_day": {EARLIER: {"total": 1, "ordinary": 1}, DAY: {"total": 4, "ordinary": 3}},
                "exceptions": {("e", DAY): 1},
            },
        )
        self.assertEqual(quota.granted, {"e"})

    def test_release_and_hold_conservatively_adjust_the_counters(self):
        burst = [seeded(f"x{i}", address="abcdef", exceptional=True) for i in range(3)]
        quota = day_quota(ordinary_day(2) + burst)
        quota.release(burst[0])
        quota.release(seeded("o0", address="o0"))
        self.assertEqual(
            tally(quota),
            {"per_day": {DAY: {"total": 3, "ordinary": 1}}, "exceptions": {("abcdef", DAY): 1}},
        )
        self.assertNotIn("x0", quota.granted)
        # A displaced story whose write failed still stands live: count it as ordinary.
        quota.hold_conservatively(burst[2])
        self.assertEqual(quota.ordinary[DAY], 2)
        # Neither a granted nor a new story is held again.
        quota.hold_conservatively(burst[1])
        quota.hold_conservatively(claim("new", address="n"))
        self.assertEqual(quota.ordinary[DAY], 2)
        # A story that was never live has no slot to release.
        quota.release(claim("new", address="n"))
        self.assertEqual(quota.counts[DAY], 3)

    def test_the_limits_are_read_when_deciding(self):
        quota = day_quota(ordinary_day(9))
        with patch.object(curation, "MAX_PER_DAY", 10):
            self.assertEqual(decision(quota, claim("new", address="n")), (True, False, None))


if __name__ == "__main__":
    unittest.main()
