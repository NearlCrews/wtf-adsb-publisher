# Site snapshots and editorial exports

The publisher consumes the engine's [collection contract](../contracts/README.md)
and derives a separate site snapshot from its private article ledger. Theme
builds and Ghost card exporters accept that site snapshot through
`public_events.validate_snapshot`; they do not accept the engine manifest as a
page-building input.

Snapshots bind projected events to their stable IDs, evidence revisions, UTC
observation intervals, measurements, article paths and retained publication
state. Their identity and totals are deterministic. Validators check field
allowlists, hashes, dates, source claims, safe references and record consistency.
Receiver details, Ghost identifiers, research working notes and private ledger
keys stay out of the public projection. Aircraft tracks are permitted.

## Review and published modes

A `review` snapshot is a private preview artifact. A `published` snapshot requires
a fresh complete Ghost inventory and matching published article identities.
Retractions and withdrawn routes remain explicit. Missing retained observations
do not authorize a deletion. Production builds reject review or synthetic
snapshots. See [operations](operations.md) for the automatic pipeline.

To inspect the existing ledger without publishing:

```sh
.venv/bin/python tools/export_public_events.py --ledger .private/autopilot/ledger.json export --mode review --output .private/review.json
node tools/ghost-theme/build.mjs --snapshot .private/review.json --output artifacts/ghost-theme/review
```

A separately supplied snapshot can be checked before rendering:

```sh
.venv/bin/python tools/export_public_events.py validate-snapshot --input .private/review.json --output .private/review.checked.json
```

The exporter writes atomically. Review outputs stay private; validated public
outputs use their intended public file mode. These commands do not call Ghost.
Use the automatic publisher to apply eligible revisions and deploy the
checked site; direct ledger approval commands are optional maintenance tools.

## Research, cards and media

Weekly dossiers retain cited source identities, applicable context and limits.
They must pass `weekly_spotlight.validate_research` and bind to the current event
revision. A complete Ghost inventory must show the exact visible research and
weekly tags before a snapshot promotes the article as a weekly pick. Failure to
qualify for that promotion does not erase the ordinary article.

`tools/export_publication_card.py` produces an HTML card, a Ghost draft bundle
or a research-update bundle for an existing owned post. Bundles preserve the
observation card, ownership/revision checks and article dates; preparing one is
not an API write. `--help` lists its required inputs. The scheduled publisher
uses the same validators and needs no per-story manual approval.

Media manifests bind maps, charts and replay files to the same saved evidence
revision and observation window. The build refuses missing or mismatched assets.
The [theme guide](../tools/ghost-theme/README.md) covers rendering, immutable page
generations, browser checks and source-specific image rules. Exported snapshots
and media do not establish an aircraft's mission, occupants or an event's cause.
