# Ghost theme and site tools

The native theme lives in `themes/wtf-adsb/`. The default test build uses invented
stories; a validated snapshot can supply real observations for private review
without changing Ghost or the detection application. Generated pages, screenshots, check results, and the
uploadable ZIP belong in `artifacts/ghost-theme/`, which is ignored by Git.

For a local preview, build the synthetic fixture and use the loopback server
below. Private and public deployment settings belong to the publisher's
[operations guide](../../docs/operations.md).

The About page in `demo-content.mjs` opens with a generic introduction that says
what the site does and names no place, hardware, or host, then describes the
writing process, data credits, and map privacy rules. A fork supplies its own
introduction, such as its receiver and software, through `about_file`.

## Site settings

The site's name, home heading, description, support link, author, About
text, and contact address are settings in `publisher.json` (see the
[operations guide](../../docs/operations.md#configuration-and-state)). The
publisher checks them and passes them to the tools it starts as `SITE_NAME`,
`SITE_TAGLINE`, `SITE_DESCRIPTION`, `SITE_SUPPORT_URL`, `SITE_SUPPORT_LABEL`,
`SITE_AUTHOR`, `SITE_ABOUT_FILE`, and `SITE_CONTACT_EMAIL`. `site-settings.mjs`
reads and checks each one once, with `SITE_ORIGIN`, and supplies the defaults:
"ADS-B findings" for the name, the theme's own heading and description, "the
site" for the author, the generic About introduction, and no support link or
contact address. `markdown.mjs` renders the About file. A production build without `SITE_NAME` stops before it writes anything.
When you run the tools by hand, set the same variables.

The theme reads the values the way Ghost supplies them, so one set of templates
serves both renderers:

- The name is Ghost's own site title, `{{@site.title}}`, in the header, the
  footer, the page eyebrow, and the error pages. The static build sets it from
  `SITE_NAME`; a Ghost-rendered site uses the title in Settings, General.
- The home heading, the description, and the support link are custom settings
  in the theme's `package.json` (`site_tagline`, `publication_description`,
  `support_url`, and `support_label`), read as `{{@custom.*}}` like the
  existing `contact_email`. The static build fills them from `SITE_TAGLINE`,
  `SITE_DESCRIPTION`, `SITE_SUPPORT_URL`, and `SITE_SUPPORT_LABEL`; a
  Ghost-rendered site reads the values set in Ghost Admin under the theme's
  design settings. The support partial renders only when `support_url` is set.
- The author is the name and slug of the author page and story bylines. The
  static build takes them from `SITE_AUTHOR`, with the slug made from the name;
  a Ghost-rendered site uses its staff user, whose slug must match.

The social images, the titles, and the About page take the name from the same
module. A card's cache key includes the name, so a new name draws new images.

## Build and check

Run these commands from the repository root. Install the root lint dependencies
with `npm ci` if they are missing, then install this toolset with
`npm ci --prefix tools/ghost-theme`. The lockfile pins the tool versions; GScan
uses the package's Node 24 binary. Run
`npm --prefix tools/ghost-theme run browser:install` to install the pinned stable
Chrome for Testing build under this toolset's ignored `node_modules` cache.
`browser-version.json` records its version, official download URL, and SHA-256;
the installer verifies both archive and executable. `CHROMIUM_PATH` overrides
that browser when needed. The supplied build targets Linux x64.

```sh
make lint-theme
make test-theme
make test-ghost
make check
```

`make lint-theme` builds the preview, validates its HTML, runs GScan on the theme
directory, and packages the ZIP. `make test-theme` runs the replay and browser
checks against the same build. `make test-ghost` renders the packaged theme in
the pinned Ghost image. All three are part of `make check`, which also covers the
repository's Python, deployment configuration, and application tests. The theme's
scripts and styles and these tools are linted and format-checked by the root
`npm run lint`, with the root ESLint, Stylelint, and Prettier settings. The
individual steps are the `build`, `check:ghost`, `package`, `check:replay` and
`check:browser` scripts, run as `npm --prefix tools/ghost-theme run <script>`.

For local inspection, run `node tools/ghost-theme/serve.mjs` and open
`http://127.0.0.1:8788/`. The server binds to loopback. `PREVIEW_PORT` changes
the port. It serves only the generated preview directory.

GScan checks Ghost's template and package requirements. Packaging reads every
theme file first and stops, leaving the previous ZIP in place, if one contains a
private hostname, a Tailscale address, the private directory, a receiver
coordinate field, or a receiver-relative field name (range, bearing, or signal
level). The built-in expression is in
`src/wtf_adsb_publisher/data/private-patterns.txt`. A site adds its own names
and hosts through `private_terms_file` in `publisher.json`, and the publisher
passes that file to these tools as `PRIVATE_TERMS_FILE`. When you run the tools
by hand, set `PRIVATE_TERMS_FILE` to the same absolute path, or only the
built-in patterns apply. The file's expression must use syntax that Python and
JavaScript share: no `(?P<name>...)`, `(?>...)`, `(?#...)`, `\A`, or `\Z`. The
literal `private_terms` list in `publisher.json` applies only in the Python
checks and in `verify-live.mjs --config`, not in these build checks. The browser
check applies the expression to each rendered page, and the production check to
every built text file.

The browser checks audit every preview route listed in the build's
`audit-routes.json` at 320, 390, and 1440 pixels: automated accessibility checks,
internal links, search, focus return, the skip link, persisted appearance,
replay controls, reduced motion, and reading without JavaScript. The routes
include the home page, the story archive, the topic index with every populated
topic and one empty one, the aircraft and operator indexes, type hubs, the
weekly archive, an author page, an immutable snapshot page, and both error
pages. A second build from `site_fixtures.py` (below) adds stories with prose,
type background and related links, airframe and operator hubs, numbered
archive pages, and older story-less events, audited at 390 and 1440 pixels. A
third, built with 45 extra stories (`--extra-count 45`), pages both the
archive and the Flight shapes topic three times; its six paged routes are
audited at 320, 390, and 1440 pixels, and the checks step through them with
and without JavaScript (see Story list pages below). A separate 117-event
fixture exercises journal pagination. The night appearance
is audited on the homepage and on a story, and the saved choice must apply
with `site.js` blocked. Each browser context fails on an uncaught page error
and on any request to another origin. The fixture gives two stories an
airframe photo, one under a map and one leading a story without one, and
neither shows the type photo; a local image stands in for Planespotters' host. No other page may load from another
origin, and no file other than a page may name Planespotters. The fixture's
newest story is an R44, a type with no photo: its home tile and journal cards
must show the local helicopter class photo, with the note, alt text, and
caption that say so, while its story page keeps the summary figure and the
credits page lists the photo among the class photos.
Replay checks also exercise gaps, fractional durations, invalid coordinates,
invalid gap flags, and redirects. These checks use local fixtures and spend no
API credits.

The media checks bind generated image assets to the exported snapshot and its
evidence versions, reject changed file hashes and unsafe SVG content, and ensure
that card images remain visible and load at every tested width. They also
match every `credits.json` record to its WebP file (hash, size, frame, and an
accepted license), refuse two class photos that claim the same type, check the
class photo picked for a set of designators, and test the airframe photo input
checks and the third-party resource scan.

`check_ghost.py` (`make test-ghost`) starts the Ghost image pinned in
`deploy/ghost/compose.yaml` with no network, a temporary SQLite database, and the
packaged ZIP as the active theme. It requests 19 public paths as the public host,
including `/ghost/` and a post's `/edit/` address.
A control run with Ghost's stock settings must show the admin origin on the home
page. The run with the safeguards from `ghost.production.example.json` must show
no private name in any body or header, no Portal or search script, a 404 for
`/ghost/` and a valid JSON search index. It removes its container and files when
it finishes. It needs Docker and the Ghost image already pulled; it never
downloads one.

## Fonts

Pages load two variable fonts as Latin-subset WOFF2 files from
`themes/wtf-adsb/assets/fonts/`: Instrument Sans for body text (29,996 bytes)
and Newsreader for headings (132,272 bytes), 158 KiB together. Both fonts use
the SIL Open Font License 1.1 with no reserved font name, and each license file
sits beside its font. The full TrueType sources and copies of their licenses
are in `tools/ghost-theme/fonts/` (SHA-256 `b24f1812…b33b1` for Instrument
Sans, `8a08d13f…7c1` for Newsreader). Social cards are drawn with the full
Newsreader source.

`partials/fonts.hbs` holds the `@font-face` rules, with `font-display: swap`,
and a preload for the body font. Both URLs come from the `{{asset}}` helper, so
the preload and the rule name the same file, versioned in production builds
and cached as immutable by the public proxy. `check-production.mjs` checks that
every page preloads it, that each font is fetched once, and that no TrueType
file ships.

The subsets keep Google Fonts' Latin range, plus the arrows and math signs the
site prints where a source font has them; fallback fonts draw the rest, such as
⌁, ◇, ◐, and ↻, which neither font has. Letters beyond that range, such as Ł and
Vietnamese letters, also come from fallback fonts. The subsets keep the shaping
features Chrome applies by default and the `tnum` and `pnum` figure features.
Instrument Sans is pinned to its default width, which the theme never changes;
Newsreader keeps its weight and optical-size axes. A pixel comparison in the
pinned Chrome found no difference from the TrueType sources for Latin-1 text,
punctuation, arrows, and math signs at weights 400 to 700, sizes 12 to 72 px, and
both figure styles; keeping `pnum` is what keeps Instrument Sans's minus sign
identical in Chrome on Linux. Regenerate the files from the repository root
with fontTools
4.66.1 and Brotli 1.2.0, run through `uvx` so nothing is installed into
`.venv`:

```sh
LATIN="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2190-21FF,U+2212,U+2215,U+2248,U+2260,U+2264-2265,U+2301,U+25C7,U+25D0,U+FEFF,U+FFFD"
work=$(mktemp -d)
uvx --from "fonttools[woff]==4.66.1" --with "brotli==1.2.0" fonttools varLib.instancer --no-recalc-timestamp \
  -o "$work/InstrumentSans.ttf" tools/ghost-theme/fonts/InstrumentSans.ttf wdth=100
for font in "$work/InstrumentSans.ttf" tools/ghost-theme/fonts/Newsreader.ttf; do
  uvx --from "fonttools[woff]==4.66.1" --with "brotli==1.2.0" pyftsubset "$font" --unicodes="$LATIN" \
    --layout-features+=tnum,pnum --name-IDs="*" --flavor=woff2 \
    --output-file="themes/wtf-adsb/assets/fonts/$(basename "$font" .ttf)-latin.woff2"
done
rm -r "$work"
```

The command is repeatable byte for byte: the outputs' SHA-256 values start
`06d2fbca` (Instrument Sans) and `1e0588ae` (Newsreader). The copyright and
license name records stay in each file.

## Content and theme boundaries

- `demo-content.mjs` contains the shared About/field-guide copy and synthetic
  story fixtures. `topics.mjs` holds the topic landings' headings, meta
  descriptions, sourced intros, and related links. Both are public prose,
  edited with Humanizer. `illustrations.mjs` makes the example SVG diagrams and
  schematic replay data. Flight measurements in the default fixture are
  invented and explicitly labeled. The aircraft background has its own source
  links.
- `build.mjs` supplies a small set of mock Ghost helpers. It does not run Ghost,
  call the publishing API, or read receiver history. Lists show 20 stories a
  page (see Story list pages below); the six-event fixture fits on one page.
  `preview.css` styles the preview notice and is not part of the theme.
- The Ghost ZIP contains only templates, theme assets, the license, and package
  metadata. It excludes the sample articles, deployment files, and development
  dependencies. `theme-manifest.json` records its files and hashes.
- The aircraft photos in `assets/images/aircraft/` are optimized, locally served
  copies of individually licensed photographs. `credits.json` records authors,
  source and license links, transformations, dimensions, and file hashes. The
  `/about/image-credits/` page is generated from this same record. Type photos are
  representative; they are not verified pictures of the observed airframe.
  Class photos show some aircraft of the same class, for cards whose type has
  no photo.
  Keep previously served photo files and their credit records while retained
  snapshots reference them. Add replacements under new hashed filenames; do not
  delete older entries from the curated catalog during an ordinary release.
  `fetch-type-photos.mjs` adds new ones (see Aircraft photos below). Photos of
  the observed airframe come from Planespotters.net and are never copied.
- The theme keeps `ghost_head`, `ghost_foot`, `content`, navigation, pagination,
  and native editor cards. Custom settings control the publication tagline and
  the initial paper/night appearance. `appearance.js` loads before the stylesheet
  and applies a reader's saved choice before the first paint. Ghost's page
  title/image setting is respected. Fonts are self-hosted Latin subsets that
  ship beside their OFL licenses (see Fonts below).
- Search belongs to the theme. `search-index.hbs` renders up to 100 recent
  stories as JSON at `/search-index/`, with text URL-encoded so a title cannot
  break the JSON. `site.js` builds the dialog and filters that index in the
  browser. It needs no Ghost API, so it works while the admin origin is private.
  The search and appearance buttons stay hidden until `site.js` runs.
- `error-404.hbs` handles missing pages. `error.hbs` is a standalone document
  for every other failure: it does not use the default layout or its helpers, and
  it never prints Ghost's error message.
- Ghost's routes are in `deploy/ghost-preview/routes.yaml`: stories at
  `/stories/{slug}/`, tags at `/topics/{slug}/`, the weekly channel at
  `/weekly/` and the theme's search index. Ghost renders the packaged theme
  natively for the offline check; the public site is the static build. Topic
  landings, hubs, sitemaps, and the feed come only from the static build.
- Each article can use prose, measurements, source links, media, and an explicit
  account of what remains unknown. Verified callsigns and registrations can
  support tracking or photo links; this preview does not invent an aircraft
  identity to make those links look populated.
- The observation card the exporter writes (`publication.py`) carries the story's
  evidence block, so Ghost and the static build show the same markup: up to five
  of the event's `headline_measurements`, each with a one-line meaning, then a
  collapsed details element with every measurement, its scope and time, and the
  evidence version, then the kind's own limitations and a link to the field
  guide. The theme styles it in `journal.css` and `event-cards.css`.

On journal pages, the compact Search button and header magnifier open the same
dialog. Filter edits remain drafts until Show results succeeds. Close or Escape
discards them, including an in-flight request. Reset changes the draft only;
Clear filters beside the active summary resets the displayed results. The
10/20/50/100 picker stays beside the result count and applies immediately.
Date ranges continue to control both the chart and matching event inventory.

The replay accepts version 1 `schematic` JSON from the same origin. Coordinates
are normalized diagram positions, not latitude and longitude. Time starts at
zero and increases; `break: true` starts a new segment. Gaps hide the moving
marker and measurements. Reported speed and altitude use the preceding sample,
while the marker interpolates within a continuous segment. Cross-origin sources
and redirects are rejected. Animation needs a click; reduced-motion mode moves
30 seconds per click. The theme also supports ordinary Ghost video cards, but
the site does not generate MP4 files. Geographic tracks use the separate
interactive replay described below.

## Images for historical cards

`event-media.py` derives static geographic track plots and measurement charts
from evidence and aircraft-track segments imported through the engine feed.
The publication runner binds media to the saved finding and evidence version;
the publisher does not decode raw archives. Lines
break at gaps over 45 seconds, rejected samples, and changes of position source.
Same-time copies of one fix from one source within 0.01 nautical miles, which
readsb writes for some MLAT positions, count as one position. A track image
describes its selected interval; it does not imply a complete flight. Alt text
names the type, registration, and nearby town, for example "Map of Cessna 172
N12345's recorded track near Coleman, Michigan: 5 positions over 2 minutes,
about 1.4 miles." The town comes from `src/wtf_adsb_publisher/places.py`, which story
facts use too, so text and map agree.
Public assets contain aircraft geometry and allowed event details only.

A formation or TCAS map also draws each other aircraft's saved track, when its
evidence carries one (`participants[].track`), in a second color with a legend.
TCAS advisories, stratospheric balloons, thermal soaring, and aerobatic practice
add a 1200 by 400 altitude profile: barometric altitude against minutes from the
first report, split where the map's track splits, with the other aircraft for an
advisory. The media manifest lists it under the event's `figures` with the role
`profile`, and the site model passes it to the page as `profileImage`.

```sh
.venv/bin/python tools/run_autopilot.py --dry-run \
  --config .private/autopilot/config.json \
  --inventory .private/ghost-inventory.json \
  --output artifacts/ghost-theme/aircraft-review
```

The builder verifies the manifest's snapshot and evidence versions, checks each
SVG's content hash, and copies the public images into the review build. A
representative type photo leads a card that has no track map or chart, and a
class photo replaces the summary figure on a card whose type has no photo (see
Aircraft photos below). The latest story has a larger image; older cards retain
images on phones.
Captions and photo credits appear on cards and article pages. Content-addressed
event images remain available alongside older snapshot pages.

## Deployment and privacy

The publisher builds from a validated snapshot, checks its media and pages,
then sends only the generated release to the private deployment receiver.
[Operations](../../docs/operations.md#deployment-templates-and-recovery) describes
the Ghost, Caddy, Tailscale, and service templates. Preserve publication state and
follow [migration](../../docs/migration.md) before replacing the old writers.

`make test-ghost` runs a disposable offline instance of the pinned Ghost image.
Its control configuration demonstrates private-admin leakage, while the guarded
configuration must keep that origin out of the tested public responses. The
installed configuration must retain those safeguards: no private-admin redirect,
Portal, stock search, or comments scripts. The native theme supplies its own
search. Audit real content and routing separately before changing a deployment.

The theme ZIP does not install site content, routes, or services. Keep the last
checked static release and back up CMS content/settings, theme, and routes for
recovery. Test configuration changes in an isolated instance before deployment.

## Site structure and search metadata

The build turns a validated snapshot into the site map below. Every slug in a
URL comes from the story engine's `context` (type, registration, and operator
slugs) or from `topics.json`; the build never derives a slug from free text.

| Page | Route | Indexed |
|---|---|---|
| Home | `/` | yes |
| Story | the event's `article_path`, `/stories/{slug}/` for new stories | with 60 words of prose sentences no earlier story used, weekly research or an exceptional event |
| Story archive | `/stories/`, `/stories/page/{n}/` (20 per page; Previous, Next, and numbered links) | yes |
| Topics | `/topics/`, `/topics/{topic}/` | a topic with an indexed story, or a sourced intro of 250 words or more |
| Aircraft | `/aircraft/`, `/aircraft/{type}/` | a type with a sourced profile, or with two or more stories of which one is indexed; the index when it lists an indexed type |
| Airframe | `/aircraft/{type}/{registration}/` | built only with two or more stories; indexed when one of them is |
| Operators | `/operators/`, `/operators/{operator}/` | an operator with three or more stories of which one is indexed; the index when it lists an indexed operator |
| Weekly picks | `/weekly/` (the first ten; later pages stay in the snapshot's `/event-pages/` archive) | yes |
| Pages | `/field-guide/`, `/about/`, `/about/image-credits/`, `/author/<author slug>/` | yes |
| Feed, sitemaps | `/rss/`, `/sitemap.xml` and five `/sitemap-*.xml` files | production only |
| Journal data | `/event-data/`, `/event-pages/`, `/search-index/` | no; robots.txt disallows them |

Pages below a threshold are still built and linked, with `noindex, follow`.
The prose gate compares a story only with stories released before it, counts
each sentence once, and treats sentences that differ only in their numbers or in
the names a template fills in (registration, callsign, address, type, operator,
place, and country) as the same, so a later story never de-indexes an earlier
one and a template about another aircraft is not new prose. Story pages carry
breadcrumbs, a topic, and type eyebrow with the reception-source badge (its
long label as the title; cards and tiles carry the same badge beside their
date), the observed and published dates, the lead visual, the story prose, "About the {type}" with sourced profile facts,
the evidence card, and related stories (at most five more about the airframe).
A track map or chart leads, else the Planespotters photo of the airframe, else
the representative type photo. A story shows one photo of its aircraft: the
type photo appears only when the story has no airframe photo. A type photo
that does not lead moves to "About the {type}"; an airframe photo under a map
keeps its 420-pixel width. `storyPhotos` in `site-model.mjs` applies these
rules in one place. A story about a disputed identity (`identity_conflict`,
`type_speed_mismatch`, `type_altitude_mismatch`, and `unregistered_address`)
or one whose identity checks found a conflict (the snapshot event's
`identity_conflict` flag) gets no airframe photo, since the photo of the listed
registration may show another aircraft. A subjectless story, such as a winds
aloft day, gets no photo at all.

Search metadata dates a story by its release time (`released_at`): JSON-LD
`datePublished` and `article:published_time`. A story exported without a
release time falls back to its observation start, the date Ghost publishes it
under. `dateModified`, `article:modified_time` and the sitemap's `lastmod`
follow `content_revised_at`, the last prose or evidence change, and otherwise
the release. The byline shows the observation and release dates, and an
"Updated" date for a revision more than a day after release. The snapshot's
`revised_at` is Ghost's `updated_at`, which also moves when an evidence card is
refreshed, so it never dates a page. About, the field guide, and the image
credits record the digest of the content their `updated` date describes, and
`check-site-model.mjs` fails when a page changes without a new date. About's
digest covers its text before the site settings are filled in, so a changed
setting does not move its date. An About file is such a setting, but its
modification time dates the page when it is later than the page's own date.

Production heads carry a title within 72 characters (the title plus " | " and
the site name while the whole fits), the meta description (a story's dek), canonical and
robots tags, Open Graph and Twitter tags, `article:*` times, and one JSON-LD
graph: `BlogPosting` and `BreadcrumbList` for stories, `WebSite` and
`Organization` on the home page, and `CollectionPage` with an `ItemList` on
lists. A topic's title uses the search phrase in `topics.mjs`. The title,
image, URL, dates published, and breadcrumbs never change when AI prose
replaces the template text; the headline and description do. Social images are
raster: a 1200x630 PNG that `social-cards.mjs` draws from the story's own
figure with the pinned Chromium, else the representative type photo when it is
at least 1200 pixels wide, which shows the type rather than the aircraft, else
the site card. An airframe photo is never a social image; `BlogPosting`
lists the same images. The same step draws a 720x480 JPEG of each drawn figure
among the home page's latest stories and uses it as the thumbnail when it is
smaller than the SVG. Uncached images draw on three tabs of one browser, each
holding a page with the font loaded once. The build caches images under
`artifacts/ghost-theme/social-cache/` by a digest of their inputs, the drawing
code, the font, and the browser build, and publishes them at
`/social/{sha256}.png` or `.jpg`. A production build stops when the pinned
browser cannot draw them. `site-icons.mjs` draws the site mark the same way
into the two root icons browsers and crawlers request whatever a page links:
`/favicon.ico`, with 16, 32, and 48 pixel PNG frames and transparent corners,
and `/apple-touch-icon.png`, an opaque 180 pixel square that iOS rounds itself.
They are cached in the same directory by the mark, the recipe, and the browser
build, so an unchanged release writes identical bytes. Article bodies carry the exporter's prose and "About
the {type}" blocks, with field-guide links from `publication.py`'s glossary;
the build adds the credited type photo to that block when a map or chart leads.

Optional event fields `story`, `context`, `released_at` and
`content_revised_at` come from the story engine. `import_snapshot.py`
validates the snapshot with `public_events.validate_snapshot` alone, so the
build accepts exactly what the exporter writes: `/stories/{slug}/` article
paths, a title, and summary equal to the story's headline and dek, and release
times only in a published snapshot. Snapshots without the story fields still
build. Topic routing reads the story engine's `src/wtf_adsb_publisher/data/topics.json`
(or `--topics FILE`); builds and checks resolve it with `topicsFile()` in
`paths.mjs`. A kind the table does not list falls back to the first topic of
its section, as `story_facts.topic_for` does.

Each build writes `routes.json`, one sorted JSON list of every generated route:
each HTML page, each file (`/robots.txt`, `/rss/`, `/search-index/`, the
sitemaps) and, by prefix, each directory of content-addressed or journal files
(`/assets/`, `/event-media/`, `/social/`, and so on). The publisher's slug collision
checks read it as a list of paths. `audit-routes.json` lists the pages the browser
checks audit. Caddy serves neither file.

`site_fixtures.py` writes invented review or published snapshots through the
real ledger and validators, with story, context, and story-less events, and
`--media DIR` adds tracks, a speed chart, and summary cards. `--extra-count N`
writes N story-less circling stories in place of 15, which pages the archive
and their topic. The production, weekly, and browser checks use it. Their scratch inputs go to
`WTF_CHECK_SCRATCH`, the ignored `.private/` when it exists, or the system
temporary directory, so they run in a worktree.

## Live checks

`verify-live.mjs` checks a published release against its snapshot and media
manifest, at 1440 and 390 pixels. It fetches every article and requires a 200,
no private text, a canonical URL, a robots directive, exactly one JSON-LD graph
with one `BlogPosting` dated by the story's release, and an `og:image` that
answers with a raster image. Weekly picks and exceptional stories must be
indexed. The sitemap index and each child sitemap must carry `lastmod` values,
list exactly the indexed stories, and leave out retired and disallowed paths.
The feed must be RSS with the newest 50 stories by release, whose GUIDs are
their event IDs, and `robots.txt` must disallow exactly `/ghost/`,
`/event-data/`, `/event-pages/`, and `/search-index/`. The image credits and
author pages must answer. The sitemaps, the journal's data generation (its
index, archive list, and every card) and each replay must hold no private text either. The
browser pass covers the home page's latest stories and search dialog,
`/stories/` with its archive and search, `/weekly/`, the latest weekly pick, a
replay, and `/about/`; both archives must also page without JavaScript. The
archive search looks for the newest story in its default 30-day window by
registration, else callsign, address, or headline; the result names the field
searched, or says why the search was skipped. Each group of checks is a named
function in the script, run in order. Launch-day routes (`/weekly-spotlights/`, `/tag/…`,
`/image-credits/`, `/author/wtf-adsb/`), `/weekly/page/2/`, and every path in an
`--expect-404` list must return 404.

No page may load anything from another origin except an airframe photo's
image named by its article's figure. Each figure must credit its photographer
and link to its Planespotters page with plain anchors, and the search index,
feed, and journal data must not name Planespotters.

Through the public proxy it also checks headers and caching: the CSP header
and its exact `img-src`, no
`X-Robots-Tag`, `no-cache` pages, and missing files, immutable versioned assets,
immutable story media with `s-maxage=0`, 404s for Ghost, its `/content/` and
`/members/` routes and release files such as `/routes.json` and
`/audit-routes.json`, and the `www` redirect. `--serve DIR`
runs the rest against a local build. With a `--base` other than `--canonical`,
the browser still visits the canonical origin and sends its requests to that
server. Requests to any other origin are refused (an airframe image gets a
local stand-in), the browser cannot resolve the public host, a redirect fails
the check instead of being followed, and `--edge` is not accepted.
`check-production.mjs` runs the checker this way through the real Caddy rules,
and again through copies that serve `/routes.json`, drop `s-maxage=0`, or leave
the photo host out of `img-src`, which must fail.

## Aircraft photos

Story pages use two kinds of photo, and cards a third. None is evidence about
the flight.

Type photos are representative pictures of an aircraft type, stored in
`themes/wtf-adsb/assets/images/aircraft/` with their credit records in
`credits.json`. `fetch-type-photos.mjs` adds them:

```sh
node tools/ghost-theme/fetch-type-photos.mjs --dry-run --snapshot STATE/snapshot.json
node tools/ghost-theme/fetch-type-photos.mjs --snapshot STATE/snapshot.json
```

It covers every type code with a `same_as` article in
`src/wtf_adsb_publisher/data/type_names.json`, every code listed in
`data/type-photo-sources.json` and, with `--snapshot`, every code in a
published snapshot. It resolves each Wikipedia article to its Wikidata item
and takes the item's image (P18), preferred rank first. A code under `files`
skips Wikidata and uses the Commons file named there, for a type whose Wikidata
image fails the checks below; `titles` names a type that the story engine's
type table does not. Commons must list the file as CC0, public domain, CC BY,
or CC BY-SA, with no restriction flag and a plain author credit; GFDL alone, the Open Government Licence, NC, and ND are
skipped. The file must be a landscape JPEG at least 700 pixels wide whose name,
description, and categories do not mark a drawing, cockpit, model, or wreck. The
original is downloaded once into `artifacts/ghost-theme/type-photo-cache/`,
checked against Commons' SHA-1, resized to 1200 pixels by the pinned Chromium,
and stored as a simple WebP with no metadata. Codes and classes that already
have a photo are skipped, so a second run changes nothing. Requests go one at a time, a
second apart, with the User-Agent `WTF-ADSB/1.0 (+<origin>/about/)`, where the
origin is `SITE_ORIGIN` (the project page when it is unset).
The report in `artifacts/ghost-theme/type-photos-report.json` gives the reason
for every skipped type or class.

The checks cannot see a picture. Look at each new photo, then record the
result in `data/type-photo-sources.json`: `descriptions` holds reviewed alt
text, the variant or model pictured (`shown`), and a full `caption` when the
default one would mislead, `authors` a credit line where the Commons author
field is not a plain name, and `rejected` a file that is not a usable photo of
the type. `articles` names the Wikipedia article for a code without a
`same_as` link, or a variant or family article whose image is usable when the
type's own is not (it takes precedence over `same_as`), and `skip` gives the
reason a code has no photo. Commit the
WebP files, `credits.json` and the sources file together, and update the
credits page's `updated` date and digest in `aircraft-photos.mjs`.

Class photos stand in on cards for a type that has no photo. Each entry under
`classes` in the sources file names a Commons file, a title ("Business jet"),
the ICAO Doc 8643 descriptions it covers (`L2J` is a landplane with two jet
engines) and, for jets, the categories from
`src/wtf_adsb_publisher/data/type_names.json` that tell a business jet from an
airliner. The tool checks, resizes, and stores them like type photos, under the
id `class-{key}`, with `classes` and `categories` in the credit record. The
build reads each designator's description and the list of gliders from the
operator-supplied `reference-aircraft-types-v1.json` in `WTF_ADSB_REFERENCE_DIR`
(the publisher sets it from `reference_dir`), and its category from
`type_names.json`. A type table that is missing or no longer matches its
recorded content hash leaves every card without a class photo, while a
mismatched `type_names.json` stops the build. The checks use a small fixture
type table. A photo whose classes include the description fits; one that also
lists the type's category wins. The seven photos cover single- and
twin-engine piston airplanes, single- and twin-engine turboprops, business
jets, airliners, helicopters, and gliders. Jets without a category, aircraft
with three or more piston or turboprop engines, gyroplanes, balloons, and group
designators get none.

A card shows the story's lead: its track map or chart, else its type photo.
When that would be the summary figure ("Aircraft record"), or the story has no
figure, the card shows the class photo instead, and the summary only when no
class photo fits. Home tiles, journal cards, and weekly picks follow this
rule; story lists and hubs carry no images. Story pages, feeds, and social
images keep the story's own lead. A class photo's alt text begins
"Representative business jet, not the aircraft in this story:", its caption
names the model shown, and a home tile adds the note "Representative business
jet" over the image. The credits page lists class photos in their own section.

Airframe photos show the observed aircraft itself and come from the
[Planespotters.net public photo API](https://www.planespotters.net/photo/api).
The publisher (`airframe_photos.py`, called by `tools/run_autopilot.py` when
its configuration sets `"airframe_photos": true`) looks up the aircraft of the
300 newest stories that have a registration, by ICAO address, else
registration. It caches only the JSON response under `STATE/planespotters/`
for at most 24 hours, and keeps a photo only when its large thumbnail is on
`t.plnspttrs.net`, its page is on `www.planespotters.net` and that page names
the story's registration. The
build (`--airframe-photos FILE`) checks the hosts, the unchanged URLs, the
size, and the credit again, compares the registration the publisher recorded
in the photo file with the story's, and puts the photo in the article page
only. The reader's browser loads the thumbnail from
the returned URL; the site never copies, proxies, or rewrites it, and it never
appears in cards, journal data, feeds, search, social images, or the snapshot.
The figure links to the photo's page with plain anchors and shows "Photo:
{photographer} / Planespotters.net" beside the image. Its caption says the
photo was taken on another occasion, not during this flight. The cache rules,
the Content-Security-Policy host, and the release guard are described in
[production operations](../../docs/operations.md#aircraft-photos-and-third-party-image-requests).

## Story list pages

Every list that runs past one page (the story archive, the topic, type, and
operator hubs, and the weekly archive) has page links above and below it. An
airframe hub lists all of its stories, and the author page its latest 20 with a
link to the archive, so neither pages. Above the list, Previous and Next sit on either side of "Page 2 of
11"; below it they flank the page numbers. The lists run newest first, so
Previous leads to newer stories and Next to older ones. The links' accessible
names say so ("Previous page (newer stories)"), and each carries `rel="prev"`
or `rel="next"`. On the first and last pages the missing step keeps its place,
muted, as `<a role="link" aria-disabled="true">` with no address, so it takes
no focus and the controls stay put from page to page. The navigation above is
labeled "Story pages, top of list" and the one below "Story pages"; the
journal's search results use "Event pages". `partials/page-steps.hbs` and
`page-numbers.hbs` draw them from `pageLinks()` in `site-model.mjs`. Ghost's
native list templates keep `partials/pagination.hbs`, which has the same steps
around "Page N of M". The steps are outlined like the site's other buttons,
with 44-pixel targets. On a phone the numbers below the list need the full
width, so Previous and Next become wide buttons above and below them, in the
order focus follows.

Static lists show 20 stories a page, the journal's default page size, and list
every page number, so each story stays within three clicks of the home page.
They need no JavaScript.

On `/stories/` the journal shows a "Stories per page" picker (10, 20, 50, or
100) above the archive; for a search the same picker reads "Results per page".
At 20 the archive's pages match the static ones. The journal draws other pages
and sizes itself from the snapshot's `archive.json`, which lists every story in
archive order with the static list's own markup. Its stories and their order
come from the snapshot that names its folder, so paging through it neither
skips nor repeats a story. The reader stays on the archive's address
(`/stories/?page=3`, `/stories/?size=50&page=2`), and Back, Forward, and reload
restore each page; a filter key in the address makes it a search. A new size
opens the page that holds the first story shown and keeps keyboard focus on the
picker, while a page link moves focus to the list's heading. Past 12 pages the
journal's number list keeps the first and last pages and three around the
current one, with an ellipsis for each gap. Without JavaScript the picker stays
hidden and the static pages step through the list. Pages kept from earlier
snapshots offered 25 a page; links that name it still work.

## Event journal redesign

The default build shows six explicitly synthetic observations across five
event types. The journal's chart and search sit on `/stories/`, above the plain
list of every story; the home page shows the latest stories, the weekly pick,
topics, aircraft, and the chart. The Dreamlifter background is the sample type
profile, shown with the cargo example.

The chart, filters, and numbered pages use one snapshot. The initial range is the
last thirty UTC dates at export time. A chosen range changes both the chart and
the event feed. Search combines keywords, registration, callsign, ICAO address,
aircraft type, event type, and recorded values; all search words must match.
Aircraft type, event type, and reception source filters can further narrow it
(`?source=mlat` lists the MLAT stories; the six families are ADS-B, ADS-R,
TIS-B, ADS-C, MLAT, and Mode S, from the public event's `source`, which the
search index carries). Registration matching ignores case and punctuation.
Date endpoints are inclusive and apply to the observation start. The page-size
picker offers 10, 20, 50, and 100 events, counting the lead within that total.
A filter change returns to page one; a size change opens the page that holds
the first result shown.

The complete event inventory is indexed, including retained events older than
the default window or the complete-history boundary. Older known counts are
marked incomplete; absent coverage stays unknown instead of zero. Longer ranges
use seven-day, monthly, or yearly groups. Chart totals and the keyboard-accessible
table reflect every matching event, independently of the displayed page.

Queries and pagination are saved in snapshot-specific URLs, including browser
Back/Forward and reload. Cards load only for the selected page. A failed or
mismatched request preserves the last complete view. Without JavaScript the
saved chart, cards, table, numbered pages, and all-history archive still work;
interactive search and arbitrary ranges require JavaScript.

`check-event-query.mjs` verifies matching, dates, incomplete history, and grouped
counts. `check-journal.mjs` also tests 117 events at all four page sizes, size
changes that keep the first result in view, the archive's pages at every size
with their Previous and Next links, keyboard steps, Back/Forward/reload,
unsafe or mismatched archive data, identity searches, historical imports, rapid
filters, Back/Forward races, phone chart widths, focus, failed assets, and
reading without JavaScript. `make check` runs both with the other browser checks.

For real observations, first produce a review snapshot with
`tools/export_public_events.py` as described in
[the public event contract](../../docs/site-snapshot.md). Then:

```sh
node tools/ghost-theme/build.mjs --snapshot .private/public-events/review.json --output artifacts/ghost-theme/history-review
```

The builder validates imported snapshots using the Python exporter before copying
any input into a served artifact. It rejects unknown fields, mismatched hashes,
inconsistent totals, and unsafe references. These preview articles use the same
`publication.event_cards` HTML that can be inserted into Ghost editor HTML cards.
Native Ghost posts do not have our custom observation fields: the observation
interval, scoped measurements, and revision identity therefore live in their
content. The offline Ghost check verifies that this content survives real Ghost
rendering.

Saved historical media and immutable snapshot pages can be copied with the
stopped publisher's private state and existing checked release. New media comes
from the imported feed. The default preview and `make check` use synthetic
fixtures. To review private evidence locally, import the engine feed into the
configured inbox, then run:

```sh
.venv/bin/python tools/run_autopilot.py --dry-run \
  --config .private/autopilot/config.json \
  --inventory .private/ghost-inventory.json \
  --output artifacts/ghost-theme/aircraft-review \
  --fetch-basemaps
```

`--fetch-basemaps` permits build-time requests to the fixed USGS Topo export
service. Subsequent builds can omit that flag and use the checked cache. A missing
or damaged cached map fails the build; it does not silently replace a background.
The unattended publisher enables missing-map acquisition automatically and uses
this same verified cache. Its media cache records the background mode so an
earlier vector image cannot replace a requested topographic map.
The cache stores the requested extent, verified response extent, acquisition time,
and PNG checksum. It is a build artifact and is not deployed. The private media
audit keeps a copy of the used receipts. Backgrounds depict current reference
geography, not conditions on the historical flight date.

After an exact cache miss, a frame can reuse one cached extent whose coordinates
differ by at most four floating-point steps. This covers small differences between
platform math libraries. Every other request field must match exactly. The lookup
keeps the original request, image bytes and receipts, and checks their integrity as
usual. Multiple matching extents fail the build. Each cache instance indexes
metadata on its first miss; an explicit fetch updates that index.

Each static track image embeds an indexed PNG of roads, towns, water, and terrain
from [USGS The National Map](https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer).
With `--webp-basemaps` (or `BasemapCache(..., webp=True)`), the pinned
Chromium re-encodes each background once as WebP, records the recipe beside
the cached PNG, and embeds whichever file is smaller. One `webp.mjs` process,
and so one browser session, encodes every background of a run. `raster_webp.py` accepts
only still, metadata-free images of the map frame. The unattended publisher
(`tools/run_autopilot.py`) turns WebP on for releases and dry runs. A dry run's
cache without downloads (`CachedBasemaps`) has `image()` as well, so it embeds
the same backgrounds a release would and records their WebP copies in the
shared cache, as a release does; a cache object with only `get()` still embeds
its PNG. A browser override (`CHROMIUM_PATH`) gets its own recipe, so its
encodings never pass for the pinned browser's. The publisher's media manifest names the mode
`usgs-topo-webp`, so images drawn over PNG backgrounds are drawn again. Turning
WebP on changes every track image's digest, and with it each social card, so it
belongs before a full regeneration.
[USGS permits reuse of the service and data](https://www.usgs.gov/faqs/what-are-terms-uselicensing-map-services-and-data-national-map).
USGS is credited in the image and on the About page. Image, aircraft geometry, and
replay use the same Web Mercator extent, with an extent check to catch automatic
server adjustments. Very short tracks retain enough map area to stay within the
source's useful scale range. Distances remain geodesic, not Mercator grid lengths.
PNG validation strips metadata and rejects external image references and active
content. Browsers fetch only the site's finished assets.

Omitting `--basemap-cache` retains the earlier offline vector backdrop, also used
for polar or date-line frames that cannot be exported as one source image. Its
pinned public sources and regeneration instructions are in [data/README.md](data/README.md).
Track images include a scale bar and approximate distance along accepted
segments. The same
accepted segments supply a content-addressed replay JSON file. It contains only
projected aircraft positions, elapsed times, cumulative distance, and allowed
saved readings. Image, event, evidence revision, and observation-window checks
bind each replay to its card. No receiver location or receiver-relative distance
is exported.

Geographic replay loads on request, starts only after Play, and supports a time
slider and speed selector. It pauses when hidden or scrolled out of view. Reduced
motion uses explicit 30-second steps. The moving marker interpolates between
accepted positions; readings stay attached to their saved report time. Gaps hide
the marker and readings and add no distance. The static SVG remains available
without JavaScript. `check-geographic-replay.mjs` checks these behaviors, including
failed loads, keyboard control, responsive layout, and dynamic search results.

Cards use labeled aircraft and measurement tables. Measurements share a time
label only when their original scope and timestamps match. A short explanation
keeps its interpretation limit visible; the remaining explanation, additional
measurements, and map notes stay in a native disclosure. The full article's
observation, aircraft, and measurement tables come from `publication.py`, so the
organization also survives native Ghost rendering. Preview replay figures are
rendered by the shared `event-visual` partial. Native publication includes replay
figures in owned editor HTML cards with their bound assets; `feature_media` is
a static renderer field, not a Ghost post field.

Search offers **Hide airport-pattern candidates**, unchecked by default. It
changes both the event list and chart, survives URL sharing and pagination, and
can be cleared without removing any observation. Candidate cards retain a label
and an explanation under Evidence and limitations. The label describes geometry;
it does not establish training, landings, a particular runway in use, or a mission.

`airport_patterns.py` requires a positively recognized fixed-wing type, at least
two closed loops measured across the exact selected analysis interval, and at
least two same-direction runway-aligned passes with an observed offset return
leg between them. Those passes and return legs must be in one continuous
accepted track segment. Source changes, rejected positions, and missing turns
cannot bridge evidence. Alignment is within 25 degrees and 0.2 nautical miles of
the runway axis at its midpoint or a gate 0.5 nautical miles before its end.
Returns must be at least 0.3 nautical miles long, 0.4–1.8 nautical miles offset,
and separated passes must be at least 90 seconds apart. At least 80% of the
analysis points must fall within 3 nautical miles of the runway midpoint, with
none beyond 5. These are conservative display rules, not published flight rules
or a statistical estimate of training probability. No altitude or receiver
position is needed. Helicopters, unknown types, and insufficient evidence remain
visible. Candidate counts depend on the validated input snapshot.

The annotation is separately versioned in the media manifest and bound to the
unchanged event evidence revision. Its runway dataset and positive aircraft-type
allowlist are pinned in `data/airport-pattern-rule-v1.json`; both renderer
languages use that rule. Media validation checks the public airport/runway,
counts, fixed-wing type, and replay bounds against the original loop measurement.
The derivation never changes the public observation schema or snapshot hash.
See [data/README.md](data/README.md) for source hashes and regeneration.

Deploy only the generated directory. Public production exports require eligible
records and a fresh, complete Ghost post inventory. Standalone exporters and
builders prepare artifacts; `tools/run_autopilot.py` performs Ghost writes and
static deployment. The 100-post native search template remains a fallback.
The generated snapshot search covers its complete event inventory; the fixture
search also includes its uncounted profile.

The browser rejects an index larger than 32 million decoded characters rather
than silently dropping older events. A capacity probe with 29,000 typical fixture
events fit within that bound; this is not a guarantee for arbitrarily long
summaries or measurement lists. Measure the selected archive before releasing
it. The Python snapshot/card import also has a 128 MiB subprocess output bound.

Private preview builds preserve previous `event-data/<snapshot>/` and
`event-pages/<snapshot>/` directories so readers can finish an older page sequence
while a new version is deployed. Deployments must preserve those folders too.
The airport filter uses `query-v2/<presentation-digest>/` assets and pages,
leaving earlier `query-v1/` payloads intact. The presentation digest includes the
renderer release, page size, evidence snapshot, media, and reference overrides in
a stable sorted-key serialization. Changed annotations, captions, image/replay
paths, or resource links therefore get a separate namespace. Browser index and
card payloads must match that generation. Current browser code can still search
retained v1 pages; their unclassified records stay visible and the new exclusion
is unavailable there. Bump the renderer release when changing rendering logic.
Production uses `--production` and requires a published snapshot without
synthetic events, the public origin in `SITE_ORIGIN`, and the site name in
`SITE_NAME`, which the publisher sets from `site_origin` and `site_name` in
`publisher.json`. Canonical links, sitemaps, the
feed, and structured data name that origin; without it, previews name
`https://example.com`. A production build adds social metadata and a robots
file, and removes the private banner and fixture assets. The first public
release excludes all prelaunch generations. Later releases retain old pages,
data, and media only while all earlier public event records remain unchanged.
A correction or withdrawal expires those generations; the 404 page links to
the latest journal. The receiver rejects a preview build over a production site.
The public Caddy proxy connects every successful release to the domain while
Ghost administration remains private. It marks versioned theme assets
(`/assets/...?v=`) immutable for a year. Content-addressed story maps, replays,
and social images get the same browser lifetime with `s-maxage=0`, so the CDN
revalidates them and a withdrawal at the origin takes effect at once. Only
successful responses are marked; a missing file is an ordinary 404, and pages
stay `no-cache`. See [production operations](../../docs/operations.md).

## Weekly spotlight

The homepage places **Most Interesting Last Week** after the latest stories,
with one researched pick from the previous completed Monday–Sunday UTC week.
The pick links to its existing article. `/weekly/` lists earlier picks; the
event remains counted once.
The empty state stays visible until a researched article qualifies.

The installed workflow uses `tools/run_weekly.py` and the automatic publisher.
It researches completed weeks on Monday, retries gaps daily, and publishes
without human draft approval. See [the weekly operations guide](../../docs/operations.md#unattended-publication-and-writing).
The commands below remain available as optional offline editorial tools.

Prepare a private shortlist from a completed daily curation report:

```sh
.venv/bin/python tools/weekly_spotlight.py nominate \
  --curation .private/daily-curation/curation.json \
  --output .private/weekly/nomination.json
```

Nomination is deterministic: exceptional events first, then interest, evidence,
and research depth. It uses daily-selected episodes and retains unresolved
important leads separately. This command does no web research and publishes
nothing. Research the sources before filling the dossier: two or more distinct
cited sources, at least one primary source, substantive context, applicability,
and remaining uncertainty. See [weekly operations](../../docs/operations.md#unattended-publication-and-writing)
and [the site snapshot contract](../../docs/site-snapshot.md). The dossier fields
and source requirements are validated by `weekly_spotlight.validate_research`.

Attach the dossier and its original private finding to an existing ledger event:

```sh
.venv/bin/python tools/weekly_spotlight.py attach \
  --ledger .private/autopilot/ledger.json --event-id EVENT_UUID \
  --research .private/weekly/research.json --finding .private/weekly/finding.json
```

The saved finding must independently pass evidence review. Its saved enrichment
must reproduce the event's public evidence; regeneration does not consult a live
watchlist. Attachment sets the existing event revision to pending. The usual
review export and preview build show its new research. In this optional manual
workflow, the ledger approval command covers the source and dossier together.
The scheduled weekly and autopilot commands apply their checks unattended.

For a new article, `export_publication_card.py --format ghost-draft` includes the
research, observation card, and weekly tags. To enrich an existing article,
export a request bundle using its current flat Ghost post object, including
Lexical content, tags, and update token:

```sh
.venv/bin/python tools/export_publication_card.py \
  --snapshot .private/weekly/review.json --event-id EVENT_UUID \
  --format ghost-research-update --ledger .private/autopilot/ledger.json \
  --post .private/weekly/current-post.json \
  --output .private/weekly/update-request.json
```

The request targets the ledger's existing Ghost post. It preserves other article
blocks, replay, publication status, and dates. If the observation facts changed,
refresh that card first; attaching research cannot leave old measurements behind.
These commands prepare bundles only and never call the Ghost API.

A fresh, complete published inventory must include `html` and `tags` as well as
the existing identity, dates, status, and visibility fields. The exporter withholds
the weekly promotion unless the article has the exact visible research block,
matching observation facts, and both weekly tags. Source JSON, notes, and Ghost IDs
stay private. A missing weekly block leaves the ordinary approved event intact.

Build the native Ghost package from that validated published snapshot:

```sh
.venv/bin/python tools/ghost-theme/package.py --snapshot .private/weekly/published.json
```

Install the resulting theme ZIP and `artifacts/ghost-theme/routes.yaml` together.
The generated channel filters for approved public revisions before pagination;
unreviewed posts with a weekly tag cannot fill or displace archive pages. Native
Ghost checks the UTC week and the live post revision at request time. Changed,
private, or unpublished posts disappear until their new version qualifies and the
package is regenerated. The default package has an empty weekly box and archive.

The static preview uses dated labels without JavaScript and hides an expired
current pick when JavaScript is available. `/weekly/` shows the first ten
picks, and its page links stay within one snapshot under `event-pages/`, so a
rebuild cannot shuffle later page requests.
The existing retention and withdrawal rules above apply to these pages too.

`check-weekly.mjs` exercises article citations, one-event counting, responsive
layout, accessibility, UTC rollover, and pagination without JavaScript. The
network-isolated Ghost check also exercises actual article updates, generated
routes, revision checks, and visibility changes. No test uses a paid writer or
the live site.
