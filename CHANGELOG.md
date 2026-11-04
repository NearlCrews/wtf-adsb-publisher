# Changelog

User-visible changes are recorded here. Application releases, plugin API
versions, and evidence contract versions are tracked separately.

## Unreleased

- `identity_conflict` scores 75, the story bar, so it fills a slot only when
  nothing else does; the earlier 70 could never publish.
- A finding that was never type-checked keeps its selection hold; only a
  failed or outdated check goes stale 30 days after the first sighting.
- Stories for six engine kinds: winds aloft days, foreign military listings,
  operator first sightings, aerobatic practice, unlisted addresses, and
  racetrack orbits. Each has its wording, facts, limitation lines, and writer
  note. Operator first sightings, winds aloft days, and unlisted addresses take
  fixed headlines. Winds aloft days, aerobatic practice, unlisted addresses,
  racetrack orbits, and both type mismatch kinds list their hypotheses.
- Two topics: Winds aloft under Performance and Unlisted addresses under Data
  quality. The Notable aircraft, First sightings, Flight shapes, and Holding
  patterns intros describe the kinds they gained.
- Validators for the new evidence: the winds aloft, foreign military, and
  unlisted address blocks, the operator first sighting, and the aerobatic and
  racetrack metrics.
- Subjectless stories. A winds aloft day names no aircraft as its subject: its
  event has an empty aircraft block and `subjectless: true`, its path is
  `winds-aloft-<d>-<mon>-<yyyy>`, and its page has a day summary card with no
  photo, type box, or map.
- Related stories. Each event lists the stories of its registration from the 60
  days before it, newest first and at most five, rebuilt from the events in the
  same export so every link carries its target's current headline. A change to
  the list is never a correction.
- `headline_measurements` on every exported event: the kind's headline
  measurement keys, for the theme. Aerobatic practice and racetrack cards lead
  with their shape metrics.
- `registration_photo` on every exported event: whether a page may show the
  photo found by the listed registration, false for a subjectless story, a
  disputed identity, or an identity conflict.
- New story facts: each kind's context line (`KIND_CONTEXT`), the usual descent
  rate for a rapid descent's type class (`TYPICAL_DESCENT_FPM`), a season
  record's margin, the registration's earlier story, and the closest separation
  in feet for formation and TCAS stories. The facts version is 4.
- A pinned display name table (`data/display_names.json`) that corrects
  database spellings in type names, watchlist associations, and operator names.
- Media for the rare kinds: formation and TCAS maps draw the other aircraft's
  track when the engine supplies one, and TCAS advisories, stratospheric
  balloons, thermal soaring, and aerobatic practice add an altitude profile
  figure (role `profile`). TCAS media fall back to the evidence's own positions
  when the engine's media parts are empty.
- A one-off explainer on Canadian addresses (`explainer:canadian-addresses`),
  written by the weekly run once three published mismatch stories with Canadian
  addresses exist. It is published at `/explainers/canadian-addresses/`, linked
  from those stories through `related`, and carried in the snapshot's
  `explainers` key.

### Changed

- Repeat cooldowns: one story of a kind and one identity story per airframe
  every 60 days; a watch type every 30 days; a notable variant, or a foreign
  military listing of one State and type, every 14 days; watchlist and unlisted
  address stories every 90 days per airframe; identity conflicts every 21 days
  per series; winds aloft days every 3 days; and go-arounds every 7 days.
  Operator first sightings are one story per operator designator, so a new
  operator on a common type is its own story, and a locally routine type never
  holds one.
- An unlisted address waits 7 days from its first sighting, then for a settled
  registry answer dated after that week, and a registry that lists the address
  blocks the story. Other pending type checks stop holding a story after 30
  days.
- Identity stories about a listed type refuse a direct ADS-B ground speed or a
  pressure altitude the type cannot produce. Tagged aerobatic practice near an
  airport or on a runway circuit, and holding under 20 minutes, are context
  only. Subjectless stories are exempt from address repeat rules. The curation
  version is 12.
- Scores: aerobatic practice and racetrack orbits 80, a rare type seen on at
  most one earlier day 75, a go-around 75, a holding pattern of 20 minutes or
  more 76, and a glider or lighter-than-air emitter category 75 when the listed
  type agrees. Fixed scores come from the kind registry: identity conflicts 70,
  holding stacks 78, winds aloft days and foreign military listings 80, and
  unlisted addresses 75. A racetrack orbit near a mapped runway scores at most
  50, like a runway circuit.
- Prose checks for new text: `filler:frame`, `dek:adds_nothing`, and
  `context:missing`, so every story quotes its kind's context line; a restated
  line still answers to the occupant, purpose, and hypothesis checks. AI prose
  may not repeat a type box fact (`box:reused`), and the type box lists the
  profile facts the text does not quote. Watchlist headlines read "linked to
  <name> by Plane Alert DB" and foreign military headlines "listed as military
  from <State>, spotted". Winds aloft days and foreign military listings carry
  required sentences, and a "-style" or "-like" compound hedges only its own
  phrase. Published stories keep the checks they were written under.
- Writer rules: stories run 90 to 200 words (`MIN_STORY_WORDS` is 90 for AI and
  template stories), quote the context line word for word, use the required
  sentence and the fixed or house headline exactly, and carry a dek with a fact
  value the headline lacks. New stories no longer end with "The entry does not
  show who was aboard."
- Weekly picks rank by the kind's rarity among live stories over 90 days, then interest,
  exceptional standing, and recency, and a kind picked in the last four weeks
  ranks after every other candidate. Research explains the event first and
  gives the type at most one sentence. Event sections and the explainer pass
  deterministic prose checks before they are saved. An explainer refused three
  times waits 30 days before its next attempt.
- Theme 0.7.0: story pages explain the evidence in plain language and show one
  photo per story. They link related stories and background explainers, lay
  out subjectless pages, and draw altitude profile figures. The field guide
  explains every kind of story, and the home page hides empty topics and a
  missing weekly pick.

## 0.1.0 (2026-11-01)

The first public release. The package identifies itself as 0.1.0, plugins
target API version 1, and the reader accepts the engine's evidence contract
1.0.0 and 1.1.0.

### Added

- An evidence inbox that imports the engine's JSON feed into SQLite. It checks
  sequence order, hashes, supported versions, duplicate records, and batch
  boundaries before it advances its cursor, and it never opens the engine's
  database. Only a fresh, complete live feed authorizes new stories; explicit
  retractions withdraw stories even while new releases wait.
- Unattended selection and publication: an editorial policy picks the
  observations worth a story, aims for at most nine stories per UTC
  observation date, and lets supported exceptional events exceed that total.
  Stories that pass the evidence and privacy checks publish without an approval
  step, and article identities, URLs, and owner edits in Ghost survive later
  revisions.
- Story writing with an OpenAI model under one daily cap for every AI request,
  with spending reserved before each request, uncertain reservations kept, and
  a pause switch. Without a key, stories publish in their deterministic
  wording.
- Weekly research that adds cited background to selected stories and marks
  them as weekly picks.
- Publication to Ghost through its Admin API and to a checked static release
  delivered over SSH, both built from one site snapshot. Production builds
  refuse review or synthetic snapshots.
- A Ghost theme with aircraft maps, flight replays, spike charts, social cards,
  its own search, an RSS feed, and optional Planespotters photos. The build
  checks pages for private names, hosts, and receiver details before release.
- Site configuration in `publisher.json`: `site_origin` for canonical links,
  sitemaps, the feed, and structured data; `site_name`, required for
  production, with an optional `site_tagline`, `site_description`,
  `support_link`, and `author_name` for the site's identity; `about_file` for
  the operator's own About page text; an optional `contact_email`;
  `private_terms_file` or `private_terms` for the names, users, and hosts no
  public text may contain, on top of built-in patterns for private network
  names, keys, and receiver fields; and `reference_dir` for operator-supplied
  reference files. Each operator supplies these settings, so no deny list,
  origin, identity, About text, or reference file is built in for one site.
- An AI-assisted setup path: [AGENTS.md](AGENTS.md) (also `CLAUDE.md`) tells
  an assistant how to run the [setup interview](docs/setup-interview.md), and
  `tools/setup.py`, a standard-library tool, writes the private configuration,
  checks it with read-only probes, and installs the timers behind an
  activation hold.
- Plugin interfaces (API version 1) for the evidence reader, editorial policy,
  writing transport, media renderer, publishing target, and deployment target.
- A Linux x64 container image with Python, Node, Chromium, and SSH, built from
  locked sources, with offline image checks and a `build.json` inventory.
  Tagged releases publish the image to the GitHub Container Registry and
  attach the inventory to the GitHub release.
- A host job runner for three user systemd timers (publish, write, and weekly)
  that keeps launch intents and completion receipts, reconciles existing
  containers before new work, never kills a running paid request, and supports
  a drain and activation hold for upgrades.
- Templates for self-hosted Ghost with Docker Compose, Caddy, a private
  administration network, daily backups, and private readership counts, plus a
  first-boot script for an Ubuntu server and a [hosting guide](docs/hosting.md).
- State and budget migration commands, a dry-run comparison, and a cutover
  runbook for sites moving from a single-process collector.
- Source and wheel builds under the MIT license.

### Known limits

- Linux x64 with Python 3.14.9 and Node 24 is the tested platform. ARM64 images
  and native Windows are unsupported.
- The aircraft-types, identity, and notable-variants reference files are not
  distributed while their redistribution terms are unresolved. Without them the
  publisher uses its built-in rules; see the
  [reference data notes](src/wtf_adsb_publisher/data/README.md#reference-data).
- OpenAI is the only built-in writing transport, and Ghost the only built-in
  publishing target. The static release needs an SSH host whose account may run
  the release receiver with `sudo`.
- Watchlist associations come from the engine's evidence and never establish
  who is aboard an aircraft. Stories report observations, metadata matches, and
  hypotheses, not confirmed missions or causes.
