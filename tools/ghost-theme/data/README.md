# Offline geographic context

`great-lakes-v1.json` is a generated regional subset of public-domain Natural
Earth, U.S. Census Bureau and OurAirports data. It covers 93°W–74°W, 38°N–50°N,
rather than a receiver location or reception footprint. Coordinates retain the
sources' longitude and latitude in decimal degrees; no datum transformation is
applied. The Gazetteer documentation does not specify a datum for its internal
points. Every source URL contains a fixed repository revision or
dataset year, and the manifest records the SHA-256 of each original download.
A separate content hash covers the derived geometry and place records.

Natural Earth provides lakes, real river segments, state/province boundaries,
and city points. Its 1:10 million outlines are generalized and do not establish
the position of a shoreline at local scale. Lake islands remain holes, and
artificial lake centerlines are omitted. The 2025 Census Michigan Gazetteer adds
745 city, village, and census-designated-place reference points, including places
too small for Natural Earth's city layer. These are the Census internal points,
not downtown locations or municipal boundaries. Original names and GEOIDs are
retained; matching Natural Earth city labels are replaced to avoid duplicates.
OurAirports supplies airport reference
points: large and medium facilities plus small airports with four-letter
identifiers. Closed airports and heliports are omitted. A nearby airport marker
does not establish where a flight began, ended, or operated.

The checked-in bundle is about 583 KB. It is read by the offline media renderer;
site visitors receive only the SVG features intersecting a particular plot.
The browser never downloads map tiles or contacts the data providers.

Source licenses and descriptions:

- [Natural Earth terms](https://www.naturalearthdata.com/about/terms-of-use/)
- [Natural Earth lakes](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-lakes/)
- [Natural Earth rivers](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-rivers-lake-centerlines/)
- [Natural Earth cities](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-populated-places/)
- [Natural Earth boundaries](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/)
- [OurAirports data and public-domain notice](https://ourairports.com/data/)
- [2025 Census Gazetteer files](https://www.census.gov/geographies/reference-files/2025/geo/gazetter-file.html)
- [Census public-access policy, including U.S. government copyright status](https://www2.census.gov/foia/ds_policies/ds027.pdf)

To reproduce the bundle, download the six manifest URLs under their `name`
filenames into an ignored research directory, then run:

```sh
python3 tools/ghost-theme/prepare_geographic_data.py \
  --input artifacts/ghost-theme/geography-research
```

The command checks the original hashes before converting anything. It performs
no network requests and requires only Python's standard library. Source
originals and download receipts stay in the ignored research directory. For a
source update, review the changed geography, update the pinned revisions and
hashes in the preparation command, and use a new dataset version.

`ourairports-runways-v1.json` is a separate regional reference for the optional
airport-pattern filter. It contains 1,837 open runway records with both endpoint
coordinates at the same pinned OurAirports revision used above. Airports marked
closed and heliports are excluded; small airfields with shorter identifiers are
included. Runway lengths outside 0.1–4 nautical miles, missing endpoints and
records outside the regional bounds are omitted. Coordinates are rounded to six
decimal places. The source does not verify that a historical flight used a
particular runway, or that an airport's layout was unchanged at the time.

Download the `airports.csv` and `runways.csv` URLs listed in that file, then run:

```sh
python3 tools/ghost-theme/prepare_airport_patterns.py \
  --input artifacts/ghost-theme/geography-research
```

The preparation command verifies both source SHA-256 hashes before producing the
bundle. `airport-pattern-rule-v1.json` records the reviewed content hash, exact
serialized-file hash and a deliberately limited positive fixed-wing type list.
Python and JavaScript validate the file before accepting annotations. A source,
type-list or classification-rule update requires a new reviewed version.

The geometric interpretation draws on the runway-aligned and offset legs in
the [FAA Airplane Flying Handbook's airport traffic patterns chapter](https://www.faa.gov/sites/faa.gov/files/regulations_policies/handbooks_manuals/aviation/airplane_handbook/09_afh_ch8.pdf).
The filter's numerical thresholds are this project's conservative selection
heuristics. The handbook does not validate them or identify a flight's purpose.

# Type photo sources

`type-photo-sources.json` is the reviewed input of `fetch-type-photos.mjs`. It
names the English Wikipedia article for type codes that have no `same_as` link
in the story engine's type table, the Commons file and title for types whose
Wikidata image is unusable, the class photos that cards use when a type has no
photo (each with its file, title, ICAO descriptions, and for jets the type-table
categories), the codes that get no photo and why, and what a visual check of
each chosen Commons file found: its alt text, the variant or model pictured, a
full caption where needed, a credit line where the Commons author field is not
a plain name, or a rejection. It holds no photographs or license text;
`credits.json` beside the photos records those. See
[Aircraft photos](../README.md#aircraft-photos).
