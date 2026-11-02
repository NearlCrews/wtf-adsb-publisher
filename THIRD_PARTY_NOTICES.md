# Third-party notices

The project's original code, documentation, contracts, and theme use the MIT
license in [LICENSE](LICENSE). Third-party code, fonts, photographs, data, and
service responses keep their own terms, and the MIT grant does not replace them.
The canonical MIT text is published by the
[Open Source Initiative](https://opensource.org/license/mit).

## Bundled components

| Material | License and retained notice |
| --- | --- |
| Ghost theme code | MIT; [theme license](themes/wtf-adsb/LICENSE) |
| Contracts and modules shared with WTF ADS-B Engine | MIT; the [engine license](contracts/ENGINE-LICENSE) lists the covered files |
| Humanizer 3.1.0 instructions | MIT; [license](src/wtf_adsb_publisher/prompts/humanizer.LICENSE) and [pinned source](src/wtf_adsb_publisher/prompts/SOURCE.txt) |
| Instrument Sans and Newsreader fonts | SIL Open Font License 1.1; notices beside the source fonts in `tools/ghost-theme/fonts/` and the web fonts in `themes/wtf-adsb/assets/fonts/` |
| Aircraft photographs | Per-image licenses, source links, and credits in [credits.json](themes/wtf-adsb/assets/images/aircraft/credits.json) |

## Bundled data

Runtime tables are in `src/wtf_adsb_publisher/data/` and map data is in
`tools/ghost-theme/data/`. Each JSON table with a `sources` list records its
sources, terms, and content hash there; keep that metadata when you copy one. The
[runtime data notes](src/wtf_adsb_publisher/data/README.md) and the
[map data notes](tools/ghost-theme/data/README.md) describe coverage and limits.

| Data | Attribution and recorded terms |
| --- | --- |
| `great-lakes-v1.json` | [Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/) (public domain), the 2025 U.S. Census Bureau Gazetteer (U.S. government work), and [OurAirports](https://ourairports.com/data/) (public domain) |
| `ourairports-runways-v1.json` | OurAirports, public domain |
| `airline_designators.json` | Operator designators and names from the Mictronics aircraft database, ODC Attribution License 1.0 |
| `reference-beacon-codes-v1.json` | FAA National Beacon Code Allocation Plan and Aeronautical Information Manual, U.S. government work |
| `reference-limits-v1.json` | Facts from cited FAA and EASA type certificate data sheets; each entry names its sheet, and the source publications are not relicensed |
| `reference-envelopes-v1.json`, `type_names.json`, `topics.json`, `airport-pattern-rule-v1.json` | Project-curated tables under the project's MIT license |
| `type-photo-sources.json` | Project-curated input for `fetch-type-photos.mjs` under the project's MIT license: Wikipedia article titles, Wikimedia Commons file names, reviewed alt text, and credit lines. The photographs it selects keep their own licenses, recorded in [credits.json](themes/wtf-adsb/assets/images/aircraft/credits.json) |
| USGS The National Map basemaps in track images | Not in this repository: the build fetches topographic backgrounds from the [USGS Topo service](https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer) and embeds them in each published track image. U.S. government work that [USGS permits reuse of](https://www.usgs.gov/faqs/what-are-terms-uselicensing-map-services-and-data-national-map); each image credits "USGS The National Map", and the About page adds that map services and data are available from the U.S. Geological Survey, National Geospatial Program |

## Operator-supplied reference data

Three reference files have unresolved redistribution terms, so they are not in
the source tree, the wheel, or the container image, and the MIT license does
not cover them. An operator who has reviewed copies supplies them through
`reference_dir`; without them the publisher keeps its built-in rules. The
[runtime data notes](src/wtf_adsb_publisher/data/README.md#reference-data)
explain how. The terms recorded in each file's `sources` list are:

| Data | Attribution and recorded terms |
| --- | --- |
| `reference-identity-v1.json` | Address allocation ranges transcribed in tar1090 (GPL-2.0-or-later), 32 military address ranges from tar1090-db (no declared license), and operator codes and nationality-mark checks from the Mictronics aircraft database (ODC Attribution License 1.0); the file keeps facts only, with no code or creative text |
| `reference-aircraft-types-v1.json` | Type designators, description codes, and wake categories from the Mictronics aircraft database (ODC Attribution License 1.0), plus an earliest-year statistic derived from tar1090-db (no declared license; built from Mictronics ODC-By and ADS-B Exchange data, as its `sources` entry records; no rows included) |
| `reference-notable-variants-v1.json` | Project-curated variant labels and matching rules, using description strings read from tar1090-db (no declared license; no rows included) |

Public text that relies on the Mictronics designator codes must keep the
attribution "Mictronics aircraft database (ODC Attribution License 1.0)".

## Services and optional sources

Wind context comes from Open-Meteo under CC BY 4.0. Stories that show its wind
keep the attribution "Weather data by Open-Meteo.com (CC BY 4.0)".

`tools/update_watchlist.py` downloads a commit-pinned
[Plane Alert DB](https://github.com/sdr-enthusiasts/plane-alert-db) list under the
Open Database License 1.0 and the Database Contents License 1.0. The downloaded
copy stays in local state and keeps those terms.

Planespotters thumbnails are external service resources. The site links to and
credits the returned photo. An API response grants no license to reuse that
photograph. USGS map receipts and other acquired caches are local build inputs
and are not part of this repository.

## Dependencies and container image

Python and npm dependencies keep their upstream notices. `uv.lock` and both npm
lockfiles record the pinned graph, and the
[dependency audit](docs/dependency-audit-2026-10-08.md) records direct-library
licenses and compatibility constraints. Recheck upstream terms before you
redistribute a package or asset; this inventory does not replace the original
licenses.

The Docker image starts from the [official Python image](https://hub.docker.com/_/python),
which carries CPython and Debian packages under their own terms. It also
contains the Node.js runtime, OpenSSH, and the checksum-pinned Chrome for Testing
build of Chromium. Their installed license and copyright files remain in the
image, and the image's package inventory records the installed versions. The
project's MIT license covers its own code and does not replace these component
terms.
