# Pinned runtime data

These bundled tables support the publisher's deterministic evidence checks and
site presentation. Each file is pinned: runtime reads verify its hashes, and a
missing or altered file switches off the checks that need it. Collection and
new enrichment belong to the engine; the publisher reconstructs stories from
frozen imported evidence. Nothing here is fetched at runtime.

## Reference data

Six reference files hold the facts behind the spotting rules. `reference.FILES`
pins each file's SHA-256, and each file records its sources, licenses, and a
canonical content hash. Reference facts describe database records and published
allocations; they never verify an aircraft's identity, operator, or mission.
Updates come from reviewed, pinned source data and must update `reference.FILES`
together with the shared evidence checks.

| File | Contents | Supplied by |
| --- | --- | --- |
| `reference-beacon-codes-v1.json` | FAA beacon-code allocations | This directory |
| `reference-envelopes-v1.json` | Conservative per-type speed and altitude envelopes, with wind allowances by altitude band | This directory |
| `reference-limits-v1.json` | Rated operating limits transcribed from type certificate data sheets. Each entry names its sheet, and a figure read from another sheet names that one as `speed_source` or `altitude_source`. | This directory |
| `reference-aircraft-types-v1.json` | Aircraft type classes, group designators, and earliest plausible years | The operator |
| `reference-identity-v1.json` | ICAO address allocations, military address ranges, nationality marks, and operator designators | The operator |
| `reference-notable-variants-v1.json` | Curated military and government variants and VIP mission call-sign prefixes | The operator |

### Operator-supplied files

The last three files mix facts transcribed in tar1090 (GPL-2.0-or-later), facts
listed in tar1090-db (no declared license), data from the Mictronics aircraft
database (ODC Attribution License 1.0), and project-curated rules. Their
redistribution terms are unresolved, so they are not in this repository, the
wheel, or the container image, and the MIT license does not cover them. See
the [third-party notices](../../../THIRD_PARTY_NOTICES.md#operator-supplied-reference-data).

Put them in a directory of their own and set `reference_dir` in
`publisher.json` to its absolute path. `load_config` passes the setting to the
processes the publisher starts as `WTF_ADSB_REFERENCE_DIR`, so the theme build
reads the same directory; tools that run without the configuration read that
variable directly. In the container, set `PUBLISHER_REFERENCE_DIR` in the
Compose environment. The template mounts it read-only at `/references`, the
value in the [example configuration](../../../examples/publisher.json).

Each lookup reads the reference directory first, then this directory. The three
operator files are read only from the reference directory, even when a copy
sits here. A file must match its pin byte for byte. One that differs counts as
missing: the publisher logs a warning and uses the bundled copy if there is
one. Without the operator files, their lookups return nothing and the built-in
rules apply. Stories then lose type class phrases and earliest-year checks,
address allocation and nationality-mark checks, and curated variant labels, and
cards get no class photos. A check that could not run is not evidence that
nothing unusual happened.

The engine reads the same files through the same `WTF_ADSB_REFERENCE_DIR`, so
one directory can serve both applications. The engine treats that variable as
a replacement for its whole data directory, so a shared directory must also
hold the three bundled files above. The engine's setup copies them there.

### Obtaining or building the files

Nothing here downloads or builds reference data. If you have reviewed copies
that match the pins in `reference.FILES`, copy them into the reference
directory. Otherwise run without them, or build your own and review the source
terms for your use. A file you build has a different hash, so it loads only
after a code change: a new pin (and a new `-v2` name for a different dataset),
the same change in the engine's `analysis/reference.py`, and regression tests.
`tests/reference_fixture.py` shows the shape of each file with a few entries.

Each file's `sources` list records the upstream URL, revision, retrieval time,
and SHA-256 of what it was built from:

- Aircraft types: designators, description codes, and wake categories from the
  Mictronics `icao_aircraft_types.zip` at revision
  `3f80a2d7c8a170352e03e29f2e8c9bd77e00d5db`. The earliest plausible year comes
  from a tar1090-db aircraft list: the first-percentile manufacture year (at
  least the third earliest) of at least 20 dated airframes, minus five years.
- Identity: address allocation ranges from tar1090's `html/flags.js` at
  revision `e784ee5ae82948f41efe3ef5c235ade0943ab8ff`, which transcribes ICAO
  Annex 10 Volume III; 32 military address ranges from tar1090-db's
  `ranges.json` at revision `fb38887c96bbaa6fa4707a96bd6fdd3c00fe6009`; and
  operator designators and the nationality-mark check from the Mictronics
  database at revision `3f80a2d7c8a170352e03e29f2e8c9bd77e00d5db`.
- Notable variants: project-curated labels and matching rules, with the exact
  description strings read from a tar1090-db aircraft list.

Public text that relies on the Mictronics designator codes keeps the attribution
"Mictronics aircraft database (ODC Attribution License 1.0)".

## Story tables

`type_names.json` (curated display names, aliases, slugs and category words),
`airline_designators.json` (operator designators and names derived from the
Mictronics database, ODC Attribution License 1.0), `topics.json` (public
topics and their sections), and `display_names.json` (corrected spellings for
database type descriptions, watchlist associations, and operator names, some
limited to registrations with a given prefix) are read by the story engine
through `story_facts.TABLES`, which checks their schema and content hash. The site
build reads the same `topics.json`.
`tools/type_name_gaps.py` lists designators seen in a journal copy or ledger that
the table does not name (its output is local traffic data and stays under
`.private/`) and merges reviewed entries.

## Private patterns

`private-patterns.txt` is one regular expression of names that no public text
may contain: Tailscale names and addresses, auth and API keys, the private
directory, and receiver coordinate and receiver-relative field names.
`private_terms.py` and the theme tools join it with the site's own terms file
(`private_terms_file` in `publisher.json`, or `PRIVATE_TERMS_FILE` for the
theme tools). It is not hashed; a change takes effect at the next run.

## Airport context

`airport-pattern-rule-v1.json` and `ourairports-runways-v1.json` are the
runtime copies of the reviewed runway reference and classifier rule in
`tools/ghost-theme/data/`. The presentation Python adapter imports this runtime
classifier; the JavaScript renderer reads the presentation copy.

The 1,837 runway records come from public-domain OurAirports data at revision
`07f86e80d3f15296c3ff971f49c33ddb331c8d8a`. The dataset records the original
download URLs and SHA-256 hashes. Coverage is 93°W–74°W and 38°N–50°N. Airport
elevations are absent. Runway proximity cannot establish height above terrain,
an actual landing, or historical runway use.

Stories name the nearest public-use airport within ten statute miles of the
aircraft's positions, as a distance only (`airports.py`). Public use means a
four-letter ident or a three-character FAA location identifier, digits included
(45G); a four-character ident with a digit (MI45) is the FAA's private-use pattern.

See [the dataset preparation notes](../../../tools/ghost-theme/data/README.md)
and [OurAirports' public-domain notice](https://ourairports.com/data/).
`tools/ghost-theme/prepare_airport_patterns.py` verifies the pinned originals
before generating the bundle. A reviewed update must keep both copies identical,
update both rule manifests, and pass the airport classifier tests. Runtime reads
verify the serialized file and content hashes.
