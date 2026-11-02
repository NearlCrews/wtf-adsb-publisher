# Development

This guide is for people who change the publisher, its theme, or its plugins,
and for maintainers who prepare releases. To install and run the publisher,
start with the [README](README.md).

## Development setup

Use Linux x64 with Git, GNU Make, ShellCheck,
[uv](https://docs.astral.sh/uv/getting-started/installation/), Docker Engine with
the Compose plugin, and Node.js 24 with npm. The validated toolchain is Python
3.14.9, Node 24.22.1, and npm 12.2.0. GScan requires the Node 24 line; the
[dependency audit](docs/dependency-audit-2026-10-08.md) records the
compatibility constraints. The package declares Python 3.12 or newer, and the
full checks use the interpreter pinned in `.python-version`.

From the checkout:

```sh
uv python install
uv sync --locked --extra dev
npm ci
npm --prefix tools/ghost-theme ci
# Install Chromium's system libraries on a fresh Linux host; this may use sudo.
tools/ghost-theme/node_modules/.bin/playwright install-deps chromium
make prepare-checks
UV_OFFLINE=1 make check
```

The two `npm ci` commands install the root lint tools and the theme tools.
`make prepare-checks` downloads the checksum-pinned Chrome for Testing build of
Chromium and pulls the pinned Ghost and Caddy test images.

## Run the checks

`make check` runs Python, configuration, and asset lint; documentation links;
evidence and publication tests; theme builds with GScan, replay, and browser
accessibility checks; the offline Ghost privacy check; and source and wheel
builds with an installed-wheel smoke test. It needs Docker, ShellCheck, Node 24,
and the Chromium build from `make prepare-checks`, but no engine checkout,
receiver, Ghost credentials, or OpenAI key. Tests use synthetic evidence and
mocked paid providers, and they never spend AI credits. The Ghost audit runs
with container networking disabled, and no production stack is started.

[CONTRIBUTING.md](CONTRIBUTING.md) lists the focused targets, such as
`make lint-python`, `make docs`, `make test`, `make test-theme`, and
`make test-ghost`.

## Build and check the image

```sh
make image
make test-image
docker image inspect --format '{{.Id}}' wtf-adsb-publisher:local
```

The Linux x64 image holds the publisher, site assets, Python, Node, Chromium,
and SSH. The build downloads pinned base images, locked dependencies, and the
checksum-verified browser; no host Python environment is needed. `make
test-image` checks the installed container with synthetic fixtures and disabled
networking, using the host's `python3` (set `HOST_PYTHON` to choose another).
The [Docker guide](docs/docker.md) covers the image commands, storage, and
derived plugin images.

## Theme tools

The Ghost theme is in `themes/wtf-adsb/`, and its build, preview, and check
tools are in `tools/ghost-theme/`. `make lint-theme` builds the synthetic
preview and packages the theme ZIP; `node tools/ghost-theme/serve.mjs` serves
that preview on `http://127.0.0.1:8788/`. Generated pages, screenshots, and the
ZIP go to `artifacts/ghost-theme/`, which Git ignores. The
[theme guide](tools/ghost-theme/README.md) covers media, maps, browser checks,
and the static release.

## Configure and import evidence

To work against a feed from a source checkout, initialize private state:

```sh
install -d -m 0700 .private .private/autopilot
.venv/bin/wtf-adsb-publisher init --state-dir .private/autopilot --available-from 2026-11-01
install -m 0600 examples/publisher.json .private/autopilot/config.json
```

Set the configuration's state, inbox, budget, feed, and credential paths to
absolute paths, and set `live_since` to the same boundary as `--available-from`.
Keep the inbox and budget databases separate. Writers refuse a missing budget
store instead of starting a new one at zero. Then import and inspect a feed:

```sh
.venv/bin/wtf-adsb-publisher ingest --feed /srv/wtf-adsb/feed --database .private/autopilot/inbox.sqlite3
.venv/bin/wtf-adsb-publisher status --database .private/autopilot/inbox.sqlite3
```

A normal publication pass also imports the configured `feed_directory` first.
The inbox cursor records receipt separately from Ghost publication, and a
duplicate batch cannot create a second article. `ingest --allow-synthetic`
accepts the offline fixtures. To move an existing deployment's ledger, budget,
and article history, follow [migration](docs/migration.md).

## Run without Docker

The scheduled jobs can also run from a source checkout. You need
[uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 24,
`openssh-client`, and the fonts the image installs (`fonts-liberation` and
`fonts-noto-color-emoji` on Debian or Ubuntu). Chromium also needs its system
libraries: `playwright install-deps chromium` installs them, or install the
packages the [Dockerfile](Dockerfile) lists.

```sh
uv python install && uv sync --locked
npm --prefix tools/ghost-theme ci --omit=dev
tools/ghost-theme/node_modules/.bin/playwright install-deps chromium
npm --prefix tools/ghost-theme run browser:install
```

Initialize the state and configuration as in the previous section, with
absolute host paths. [Operations](docs/operations.md) lists the three job
commands and the `PATH` they need; schedule them as the timer templates in
`deploy/ghost-preview/` do.

## Publish and extend

[Operations](docs/operations.md) covers dry runs, publication, AI controls,
backups, and recovery. Stale, incomplete, synthetic, or unsupported evidence
holds new releases, while explicit retractions still withdraw affected stories.
Identity articles keep database labels, address-derived registrations, and
callsigns apart. Runtime writers use the pinned
[Humanizer instructions](src/wtf_adsb_publisher/prompts/humanizer.md) alongside
deterministic factual and privacy checks.

`.venv/bin/wtf-adsb-publisher plugins` lists the installed readers, policies,
writers, renderers, publishing targets, and deployment targets without importing
their code. The [plugin guide](docs/plugins.md) describes the six roles, and
the [contracts](contracts/README.md) describe the engine feed the reader
accepts.

## Rules for changes

- Keep Python code in `src/wtf_adsb_publisher/`, the theme in `themes/wtf-adsb/`,
  tests in `tests/`, maintenance commands in `tools/`, and deployment templates
  in `deploy/`.
- Consume the versioned JSON evidence contract. Normal publishing must not
  import the engine, open its database, or read receiver archives.
- Preserve article IDs, URLs, Ghost ownership markers, publication history,
  cooldowns, uncertain-write recovery, and AI spending reservations across
  changes and migrations.
- Publication is unattended. Do not add per-story manual approvals. Stale,
  incomplete, synthetic, or unsupported evidence must hold new publication
  through automatic checks.
- Keep observed telemetry, metadata matches, hypotheses, and confirmed facts
  apart in public text. A watchlist association never proves who is aboard.
- Administration, SSH, and service-to-service publishing stay on a private
  network. Keep private hostnames and receiver information out of generated
  pages.
- Runtime writers apply the pinned Humanizer instructions to prose. Humanizer
  does not check facts.
- `make check` must pass, and tests never spend AI credits.
- Pin dependencies to exact, verified stable versions within upstream
  compatibility constraints, and refresh the lockfiles with every change.
- Keep credentials, `.private/`, databases, raw archives, and private network
  state out of version control. A local `.private/` stays mode 0700.
- `tools/setup.py` uses only the standard library. Keep it in step with
  [docs/setup-interview.md](docs/setup-interview.md) and the unit templates in
  `deploy/ghost-preview/`; `tests/test_setup_tool.py` compares them.

## Shared files

`wire.py`, `kinds.py`, `enrichment_rules.py`, and the evidence JSON Schemas are
byte-identical copies of the engine's files. A change to one must land in both
repositories, followed by `python tools/record_shared_digests.py` in each
checkout. `tests/test_shared_digests.py` fails until the digests in
`sync-state.json` match. [CONTRIBUTING.md](CONTRIBUTING.md#shared-files) lists
the exact paths.

## Dependencies

`.venv/bin/python tools/check_dependencies.py --node-major 24` compares the pins
with current upstream releases over the network and changes nothing. `uv.lock`
is authoritative for Python; keep `requirements-dev.txt` consistent with it. The
browser version and checksum are in `tools/ghost-theme/browser-version.json`.

## Release steps

1. The repository is `NearlCrews/wtf-adsb-publisher`, and its owner appears in
   `README.md`, `CONTRIBUTING.md`, `pyproject.toml`,
   `src/wtf_adsb_publisher/site_origin.py`, `src/wtf_adsb_publisher/registry_http.py`,
   and `tools/ghost-theme/site-settings.mjs`. A fork that publishes under another
   owner replaces those mentions; `git grep -n 'github.com/NearlCrews'` lists
   them, and the tests read `site_origin.FALLBACK_CONTACT`, so they follow.
2. Set the version in `pyproject.toml` and `src/wtf_adsb_publisher/__init__.py`.
3. Move the Unreleased notes in [CHANGELOG.md](CHANGELOG.md) into a dated
   section for the version.
4. Run `make check`, `make image`, and `make test-image` on a clean checkout.
5. Push the tag `vX.Y.Z` on the release commit. The release workflow checks
   the tag against `pyproject.toml`, runs `make check` and the image checks,
   pushes `ghcr.io/<owner>/wtf-adsb-publisher` with the version tag, and
   creates the GitHub release from the matching CHANGELOG section
   (`tools/release_notes.py`), with the image digest, the image's `build.json`,
   the source distribution, and the wheel attached. Only after the release
   exists does it tag the image `latest`. A version that Python's packaging
   rules call a pre-release, such as `0.2.0rc1`, `0.2.0b1`, or `0.2.0.dev1`, is
   marked as a pre-release and never moves `latest`.

## Public history

This repository began from a single commit made with
`tools/export_public_tree.py`, which copies one commit's tracked files, leaves
out private and generated paths, and refuses to finish if any file or path name
contains a string from a private list. The private predecessor history is not
published. To repeat the export from a private checkout, run
`python3 tools/export_public_tree.py --source CHECKOUT --commit COMMIT --destination NEW_DIR --forbidden TERMS_FILE --author "NAME <EMAIL>"`,
where `TERMS_FILE` lists one forbidden string per line. Without `--author`, the
tool takes the identity from `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`,
`GIT_COMMITTER_NAME`, and `GIT_COMMITTER_EMAIL`, and refuses when any of them is
unset, so Git's configured identity never reaches the public commit.

## Contributing and support

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Use
[SUPPORT.md](SUPPORT.md) for questions and [SECURITY.md](SECURITY.md) for
private vulnerability reports. The [documentation index](docs/README.md) links
every guide.
