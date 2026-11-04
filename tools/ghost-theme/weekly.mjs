import { dateLabel } from "./journal.mjs";

const DAY = 86400000;
const iso = (value) => new Date(value).toISOString().slice(0, 10);

export function weeklyView(snapshot, media = {}) {
  const day = new Date(snapshot.as_of_utc);
  day.setUTCHours(0, 0, 0, 0);
  const monday = day.getTime() - ((day.getUTCDay() + 6) % 7) * DAY;
  const week = iso(monday - 7 * DAY);
  const picks = snapshot.events
    .filter((event) => event.weekly_spotlight)
    .map((event) => {
      const start = event.weekly_spotlight.week_start;
      const end = iso(Date.parse(start) + 6 * DAY);
      const visual = media[event.id] || event.media;
      return {
        title: event.title,
        url: event.article_path,
        week: start,
        weekLabel: `${dateLabel(start)} – ${dateLabel(end)} UTC`,
        reason: event.weekly_spotlight.reason,
        feature_image: visual?.src,
        feature_image_alt: visual?.alt || "",
        review: snapshot.mode === "review",
        synthetic: event.synthetic,
      };
    })
    .sort((a, b) => b.week.localeCompare(a.week));
  return {
    pick: picks.find((pick) => pick.week === week),
    picks,
    validFrom: `${iso(monday)}T00:00:00Z`,
    validUntil: `${iso(monday + 7 * DAY)}T00:00:00Z`,
  };
}
