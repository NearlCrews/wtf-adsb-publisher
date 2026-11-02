# Changelog

User-visible changes are recorded here. Application releases, plugin API
versions, and evidence contract versions are tracked separately.

## Unreleased

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
