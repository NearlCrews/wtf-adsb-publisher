# Publisher operations

Use the [Docker image and job runner](docker.md) for scheduled deployment. The
maintenance commands below use the source checkout and environment described in
[setup](../DEVELOPMENT.md#development-setup). Deployment credentials and state stay
in `.private/` or another protected directory. Administration, SSH, and publishing
traffic use a private network; generated pages contain no private hostnames or receiver
coordinates.

## Command environment

After installing dependencies, set the same Python and Node paths used by the
services before running maintenance commands from the checkout:

```sh
export PATH="$PWD/.venv/bin:$PWD/tools/ghost-theme/node_modules/node/bin:$PATH"
```

## Configuration and state

Copy [publisher.json](../examples/publisher.json) into a private configuration
file. JSON paths are literal; use absolute paths when invoking services.

| Setting | Purpose |
| --- | --- |
| `state_dir` | Article ledger, receipts, snapshots, research, status, and caches. |
| `inbox_database` | Durable imported engine evidence and receipt cursor. |
| `budget_database` | Existing publisher spending and uncertain reservations. |
| `contact_email` | Optional public contact address, such as `contact@example.com`: a plain address with no name or angle brackets. The footer and the Organization structured data show it only when set. |
| `feed_directory` | Read-only delivered engine manifest and immutable batches. |
| `live_since` | Retained publication observation boundary. |
| `max_feed_age_seconds` | Freshness bound for new live publication. |
| `allow_replay_publication` | Explicit opt-in for historical replay; false by default. |
| `extension_schemas` | Reviewed, digest-pinned local enrichment schemas. |
| `ghost_origin`, `ghost_key_file` | Private Ghost Admin API origin and key file. |
| `weekly_key_file` | API key used by paid writing/research. |
| `preview_ssh_host` | Private SSH destination for the checked static release. |
| `private_terms_file` | Absolute path to a mode 0600 file with one regular expression on one line: names, users, and hosts no public text may contain. The file must not be empty, and the expression must not match the empty string. |
| `private_terms` | Literal strings refused in public text, alongside the file, by the Python checks and `verify-live.mjs --config`; the Node build checks read only the file. |
| `production` | Production release checks; false still permits Ghost writes in a real pass. |
| `reference_dir` | Optional absolute path of the operator-supplied reference files. The publisher passes it to the tools it starts as `WTF_ADSB_REFERENCE_DIR`. Without it, the built-in rules apply; see [reference data](../src/wtf_adsb_publisher/data/README.md#reference-data). |
| `site_origin` | Public site origin, such as `https://example.com`: lower case, with no port, path, or trailing slash. Required when `production` is true; otherwise `https://example.com`. |
| `site_name` | The site's name in page titles, the header and footer, the error pages, the About page, and the social images: one line of up to 60 characters. Required when `production` is true; otherwise "ADS-B findings". |
| `site_tagline` | Optional home page heading, one line of up to 120 characters. Without it, the theme's own heading applies. |
| `site_description` | Optional line under the home heading, in the footer, and in the home page's meta description and feed: one line of up to 160 characters. Without it, "Unusual aircraft and odd ADS-B readings from one receiver". |
| `about_file` | Optional absolute path to a Markdown file, which may hold HTML blocks, of 1 to 65536 bytes of UTF-8 text. Its text replaces the About page's generic introduction; the sections on writing, credits, and maps stay. A path to `/dev/null` counts as unset. |
| `support_link` | Optional support or donation link, `{"url": "https://...", "label": "..."}`. The header, the footer, and the About page show it only when it is set. The address is `https://` with a lower-case host name and no port; the label is up to 40 characters. |
| `author_name` | Optional name on the author page and story bylines, up to 60 characters with at least one letter or digit. Its lower-case, hyphenated form is the author page's address. Without it, "the site". |
| `plugins` | Optional selections for the six [plugin roles](plugins.md). |

The example's `private_terms_file` value is a placeholder. Point it at a file
the publisher can read, or delete the key. In the container, set
`PUBLISHER_PRIVATE_TERMS_FILE` in `compose.env` to the host file, and set the
key to `/config/private-terms.txt`, where the template mounts it. The
built-in patterns (Tailscale names and addresses, auth and API keys, the private
directory, and receiver field names) apply whether or not either key is set.
The publisher passes the file to the theme tools it starts as
`PRIVATE_TERMS_FILE`. Both Python and JavaScript compile its expression, so use
syntax they share: no `(?P<name>...)`, `(?>...)`, `(?#...)`, `\A`, or `\Z`,
and no global flag such as `(?i)`, since matching already ignores case. To add
no terms of your own, delete the key rather than leave the file empty.

Canonical links, sitemaps, the RSS feed, structured data, and the absolute links
in owned article blocks all name `site_origin`, so it must match the `url` in
Ghost's configuration. Outside requests (Planespotters, Wikimedia, and map
tiles) name `<site_origin>/about/` in their User-Agent. The
publisher passes the origin to the tools it starts as `SITE_ORIGIN`, and a
production build without it stops. `ghost_origin` is a separate setting: the
private Admin API address. The publisher passes `contact_email` to the
theme build as `SITE_CONTACT_EMAIL`. A Ghost-rendered site reads the theme's
own "Contact email" setting instead, so set the same address there in Ghost
Admin. The site's identity travels the same way: `site_name`, `site_tagline`,
`support_link`, and `author_name` reach the theme build as `SITE_NAME`,
`SITE_TAGLINE`, `SITE_SUPPORT_URL` and `SITE_SUPPORT_LABEL`, and `SITE_AUTHOR`,
and a production build without a name stops. `site_description` and
`about_file` reach it as `SITE_DESCRIPTION` and `SITE_ABOUT_FILE`. A
Ghost-rendered site shows Ghost's own site title and staff user and the theme's
"Site tagline", "Publication description", "Support url", and "Support label"
settings, so set the same values in Ghost Admin, and write its About page there.
The readership summary (`tools/site_stats.py`) runs on the web host without
`publisher.json`, so its systemd unit sets `SITE_ORIGIN` itself.

The About file uses a small part of Markdown: paragraphs, `#` headings (one or
two marks make a section heading and more a subsection), `-` and numbered
lists, links, `**bold**`, `*italic*`, and `` `code` ``. A block that starts
with `<` passes through as HTML. As on every page, the release checks refuse
private terms and anything loaded from another site, such as an image or a
script. In the container, set `PUBLISHER_ABOUT_FILE` in
`compose.env` to the host file and `about_file` to `/config/about.md`, where the
template mounts it; with no file, the template mounts `/dev/null` there, which
counts as unset. A later modification time than the page's own date becomes
its sitemap date.

Keep state mode 0700 and credentials mode 0600. The maintenance commands set a
private umask for newly created directories. Status reads require an existing
inbox. Runtime writers refuse a missing or uninitialized spending database;
restore the correct budget path and history instead of creating a replacement
at zero. Explicit `init` is for a new deployment; use [migration](migration.md)
for existing spending and publication history.

## Inventory and dry runs

Export a complete Ghost inventory through its private Admin API. This reads
Ghost without modifying posts:

```sh
.venv/bin/python tools/export_ghost_inventory.py --config .private/autopilot/config.json --output .private/ghost-inventory.json
```

Choose a new output path if an inventory already exists. Use the saved inventory
for an offline publication comparison:

```sh
.venv/bin/python tools/run_autopilot.py --config .private/autopilot/config.json --dry-run --inventory .private/ghost-inventory.json --output artifacts/ghost-theme/comparison
.venv/bin/python tools/run_public_writer.py --config .private/autopilot/config.json --dry-run
.venv/bin/python tools/run_weekly.py --config .private/autopilot/config.json --plan
```

Dry runs make no paid requests or publication writes. A rendering comparison
needs the verified media cache that a real release would use. Missing required
cached assets stop the build. Generated comparison files stay under `artifacts/`.
`--no-deploy` on a real autopilot pass still writes to Ghost.

## Unattended publication and writing

After migration validation, one active timer set may run:

```sh
.venv/bin/python tools/run_public_writer.py --config .private/autopilot/config.json
.venv/bin/python tools/run_weekly.py --config .private/autopilot/config.json
.venv/bin/python tools/run_autopilot.py --config .private/autopilot/config.json
```

The first two commands can buy AI requests within the shared configured budget.
The final command writes to Ghost and deploys the checked static site. Paid
writers reserve spending before requests and retain uncertain reservations.
Keep the [AI environment settings](../deploy/ghost-preview/ai.env.example)
consistent across those writers.

Pause or resume paid work for the shared budget store:

```sh
.venv/bin/python tools/run_public_writer.py --config .private/autopilot/config.json --pause-ai
.venv/bin/python tools/run_public_writer.py --config .private/autopilot/config.json --resume-ai
```

A migrated store starts paused, including a destination whose migration failed.
Reconcile outstanding requests and fix the migration before resuming. Pausing AI
does not stop deterministic publication or withdraw existing stories. Eligible
observations need no manual draft approval. The standalone ledger and weekly
editorial helpers are optional maintenance workflows.

## Story inputs and reference upkeep

Each pass loads two inputs from `state_dir` beside the evidence: sourced type
profiles and cached Wikidata answers for watchlist entry links. Before staging, a
real pass looks up a few uncached entry links, the candidates' first and then
those of records already in the ledger; a failed lookup leaves the warning
`entity_lookup_failed` and publication continues. Dry runs and simulations only
read the cache. To have later passes look every cached answer up again while
stories keep the old answer until a fresh one replaces it:

```sh
.venv/bin/python tools/run_autopilot.py --config .private/autopilot/config.json --refresh-entities
```

Type speed norms arrive with the evidence (contract 1.1.0, `context.type_norms`);
a story keeps the first norm it was staged with. The publisher computes none. A
speed story staged from a 1.0.0 observation, which names no norms, is still
staged, without one; the pass leaves the warning `type_norm_unexported` with the
story keys in its status.

`tools/update_watchlist.py` fetches a reviewed, commit-pinned Plane Alert CSV and
its license into a private cache (`.private/watchlists` by default) and writes
the `current.json` pin. The publisher runtime does not read it: watchlist matches
arrive in the engine's evidence. The engine's watchlist enrichment consumes the
pin through its references directory (its Compose file reads
`WATCHLIST_CACHE=/references/watchlist.json` from `ENGINE_REFERENCE_DIR`), so
install the pin there for new matches. Nothing fetches the CSV at runtime.

`tools/type_name_gaps.py` lists aircraft type designators the story engine
cannot name, from a read-only copy of the engine journal or a ledger copy,
into a gap list under `.private/`, and merges reviewed names into
`type_names.json` with its content hash. `tools/audit_exceptions.py` reads a
ledger without changing it and lists where the stored exception decision changes
a reader's answer; an unexpected difference exits with status 1.

`tools/kinds_golden.py` and `tools/publish_golden.py` record the kind tables and
the publication decisions of named scenarios; `tests/test_kinds_golden.py` and
`tests/test_publish_golden.py` compare fresh recordings with
`tests/fixtures/`. Record a fixture again with `--write` only when a change to
those tables or decisions is intended.

`tools/fuzz_wire.py` mutates feed observations at random and compares
`wire.validate_observation` with the same checks over the complete published
schema; any disagreement exits with status 1. It is offline and reads the example
batch, or the batches of a feed directory given with `--feed`.

## Story kinds, holds, and explainers

Six kinds from the engine have stories: `winds_aloft_day`, `foreign_military`,
`first_operator_here`, `aerobatic_practice`, `unregistered_address`, and
`racetrack_orbit`. Two topics are new: Winds aloft (`/topics/winds-aloft/`,
under Performance) and Unlisted addresses (`/topics/unlisted-addresses/`, under
Data quality). Foreign military listings join Notable aircraft, operator first
sightings join First sightings, aerobatic practice joins Flight shapes, and
racetrack orbits join Holding patterns.

A winds aloft day is a subjectless story. It is about the day, and the aircraft
it names are examples. Its public event has an empty `aircraft` block and
`subjectless: true`, and its article path is
`/stories/winds-aloft-<d>-<mon>-<yyyy>/`, with `winds-aloft-2-<d>-<mon>-<yyyy>`
for a second story of that date. The page gets a day summary card in place of a
track map and has no photo, type box, or type research. No address-keyed repeat
rule holds a winds day, and a winds day holds no other story by address; only
its three-day series rule applies.

An unlisted address waits before it can publish. For seven days after its first
sighting the selection report gives `registry_lag`. After that it gives
`type_check_stale` until a registry answer dated at least seven days after the
first sighting arrives. A registry that lists the address blocks the story
(`registry_lists_address`), and this kind's check never goes stale. For every
other kind, a pending type check stops holding a story 30 days after the first
sighting and then counts as unavailable. Identity stories about a listed type
refuse a direct ADS-B ground speed the type cannot reach, or a pressure altitude
more than 2,000 ft above its ceiling (`reading_implausible`); a published story
that fails this gate is suspended at the next pass. Aerobatic practice tagged
near an airport or on a runway circuit, and holding shorter than 20 minutes, are
context only.

The weekly run writes the Canadian address explainer once, when three published
type mismatch stories about addresses in Canada's allocation exist. It saves
the receipt as `weekly/explainer-canadian-addresses.json` under `state_dir`,
records the page in the ledger under `explainers`, and reports its state under
`explainers` in the run status and in `--plan`. A failed attempt waits a day.
After three refusals the explainer is recorded as `given_up` with its date and
is tried again, once, only after 30 days; `--plan` shows it as given up.
Each export links the covered stories to `/explainers/canadian-addresses/`
through `related` and carries the page in the snapshot's `explainers` key.
Without `reference_dir`, no address resolves to an allocation, so no explainer
is written.

### Upgrading to these kinds

- Copy the new `deploy/ghost-preview/receive-autopilot.py` to the static release
  host before the first release. It never counts a change to an event's
  `related` list as a correction, and it accepts `headline_measurements` and
  `registration_photo` on an event that lacked them. Any other change is still a
  correction to the receiver: revised template text, a story rewritten for its
  new context line, and a story suspended by the plausibility gate all count.
- The first refresh after the deploy revises stories whose facts changed: every
  kind gained a context line, and a story with an earlier story of its
  registration within 60 days gained a repeat fact and related links. A story
  still held back is skipped by `refresh_prose`, so it gains no related links,
  context line, or `type.profile` until it is released.
- Stories published earlier keep their older frames. The new prose checks
  apply to new text only.
- Records rendered by this release carry `type.profile` in their routing, which
  earlier releases refuse. A rollback needs a routing migration that removes it.
- `curation.VERSION` is 12. Stored decisions keep their exceptional standing, so
  the change does not re-evaluate published stories.

## Readership statistics

The public proxy writes a JSON access log of the public host only
([Caddyfile](../deploy/ghost/Caddyfile)): time, client address, method, path
without its query, status, user agent, referrer host, and the country code a
proxy in front supplies (Cloudflare's, in the template). Request headers,
cookies, and response headers are dropped; files roll at 1 MiB and are deleted
after 14 days. The
[stats service](../deploy/ghost/wtf-adsb-stats.service) and its
[timer](../deploy/ghost/wtf-adsb-stats.timer) run `tools/site_stats.py`, copied
to `/opt/wtf-adsb/site_stats.py`, at 00:10 UTC to summarize the previous UTC day
into private, cookieless aggregates under `/opt/wtf-adsb/stats`, without network
access. When you install the unit, change its `SITE_ORIGIN` line from
`https://example.com` to your `site_origin`, or your own pages count as
referrers. Output holds no client address, hash of one, query string, or full user
agent. The job fails, rather than skips, when the log is missing.

## Deployment templates and recovery

The [Ghost Compose file](../deploy/ghost/compose.yaml) contains the CMS, database,
public proxy, and private network client. The [static-site Compose file](../deploy/ghost-preview/compose.yaml)
serves checked releases on loopback behind the proxy. [Systemd templates](../deploy/ghost-preview/wtf-adsb-autopilot.service)
use `~/src/wtf-adsb-publisher` and `/srv/wtf-adsb-publisher/config/jobs.json`;
writer and weekly service/timer pairs sit beside the autopilot pair. Adapt paths
and secrets to the deployment before installation. Installing a template must
not enable a second writer set. Leave the networks and secrets of any other
Compose project on the host with the project that owns them.

The services use the host's standard-library Python to supervise immutable
application images. Node, Chromium, and application dependencies run inside the
image. Source Make checks still select the theme's pinned Node binary through
`PATH`. The system-wide Node installation need not change. See
[container jobs](container-jobs.md) for launch holds, overdue alerts, startup
reconciliation, and the weekly follow-up.

The installed Ghost settings must match the privacy safeguards in
[ghost.production.example.json](../deploy/ghost/ghost.production.example.json):
no private-admin redirect and no Portal, stock search, or comments scripts. The theme
supplies its own search. `make test-ghost` checks these settings against the
pinned image; it does not establish the running site's configuration.

Back up the whole stopped publisher state, configuration, AI environment, and
checked static release. Preserve the inbox, ledger, spending store, receipts,
research, and media caches together. Copying an open SQLite file can miss its WAL;
use SQLite's backup API or stop its writers first. [Ghost backup templates](../deploy/ghost/backup.sh)
cover CMS state separately. Keep recovery copies private. After a migration,
retain the previous project until [cutover and rollback checks](migration.md) pass.

Monitor imported cursor/health with the status command, service exit status, and
private per-run reports. A fresh import alone does not prove Ghost or static
publication succeeded. On an uncertain Ghost write, preserve receipts and let
ownership/revision reconciliation run; do not delete state to force a retry.

## Aircraft photos and third-party image requests

With `airframe_photos: true`, the publisher maintains a private Planespotters
response cache for at most 24 hours. It checks the returned thumbnail and photo
page hosts, registration, and credit, then adds the photo only to the matching
article page. The browser loads that thumbnail from `t.plnspttrs.net`; the site
never proxies it or adds it to feeds, search, social images, or the evidence feed.
The public proxy permits that host in its image policy. Other page resources
remain on the site's own origin. See the [theme guide](../tools/ghost-theme/README.md)
for media caches and release checks.
