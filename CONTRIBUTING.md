# Contributing

Bug reports, documentation fixes, and plugins are welcome. Read the
[code of conduct](CODE_OF_CONDUCT.md) and use [private reporting](SECURITY.md)
for vulnerabilities or disclosures of private receiver data.

## Set up a checkout

Follow the [development setup](DEVELOPMENT.md#development-setup), which installs
the Python, Node, and browser tools and runs `make check`. The checks need no
engine checkout, receiver, Ghost credentials, or OpenAI key, and they must never
spend AI credits. Useful focused targets:

```sh
make lint-python
make docs
make test
make test-theme
make test-ghost
make test-wheel
make image
make test-image
```

The image checks test the installed container with synthetic fixtures and
disabled networking. See the [Docker guide](docs/docker.md) for the image layout.

## Make a change

Describe the behavior or problem before proposing an implementation. Keep runtime
code in `src/wtf_adsb_publisher/`, command-line wrappers in `tools/`, tests in
`tests/`, deployment templates in `deploy/`, and the Ghost theme in
`themes/wtf-adsb/`. The publisher reads the engine's versioned JSON feed. It must
not import the engine or open its journal. The [plugin guide](docs/plugins.md)
describes the extension interfaces.

Preserve evidence validation, private-data exclusions, article identity, Ghost
ownership, and recovery from uncertain writes. Budget reservations must survive
failed or uncertain provider calls. Eligible stories publish automatically, so
do not add a manual per-story approval step.

Fixtures must be constructed examples. Do not add real journals, raw archives,
credentials, or private receiver details to tests, issues, or attachments. A
story must keep received telemetry, metadata matches, hypotheses, and confirmed
facts distinct.

Write prose with [Humanizer's pinned instructions](src/wtf_adsb_publisher/prompts/humanizer.md)
and keep factual claims under deterministic checks. Add a targeted regression
test for each changed invariant, update the relevant guides and
[CHANGELOG.md](CHANGELOG.md), and run `make check` before review. Keep cleanup
focused; avoid renaming public interfaces or reformatting unrelated files.

## Shared files

These files are byte-identical with the
[WTF ADS-B Engine](https://github.com/NearlCrews/wtf-adsb-engine) repository:

- `src/wtf_adsb_publisher/wire.py`
- `src/wtf_adsb_publisher/kinds.py`
- `src/wtf_adsb_publisher/enrichment_rules.py`
- `contracts/*.schema.json` and their packaged copies in
  `src/wtf_adsb_publisher/contracts/`

They are MIT-licensed like the rest of the project, and
[contracts/ENGINE-LICENSE](contracts/ENGINE-LICENSE) lists them. A change to one
must land byte for byte in both repositories. Afterwards, run this in each
checkout:

```sh
python tools/record_shared_digests.py
```

It updates the digests in `sync-state.json`, and `tests/test_shared_digests.py`
fails until they match the files.

## Dependencies and packaging

Check current upstream versions separately from the offline tests:

```sh
.venv/bin/python tools/check_dependencies.py --node-major 24
```

That command uses the network and makes no changes. Record any compatibility
constraint in the dependency audit, update the exact manifest pins, and refresh
their locks. `uv.lock` is authoritative for the Python environment; keep the
pinned `requirements-dev.txt` maintenance list consistent with it. Browser
version and checksum pins live in `tools/ghost-theme/browser-version.json`.

The wheel holds the Python runtime resources. Publishing and site tooling also
need the source checkout, the Node tools, and the theme assets, which the Docker
image bundles. Test a changed bundle in its installed form.

## Pull requests and licensing

Use the pull request template to explain the problem, the resulting behavior,
and the checks you ran. Keep private logs and state out of screenshots and
attachments. Maintainers may request changes or decline additions outside the
publisher's scope.

Contributions to the project's original code and documentation use its
[MIT license](LICENSE). Preserve existing copyright and
[third-party notices](THIRD_PARTY_NOTICES.md), and identify any material that
needs different terms. This repository requires no contributor license
agreement or sign-off.
