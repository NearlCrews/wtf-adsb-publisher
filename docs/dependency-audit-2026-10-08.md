# Dependency audit, 8 October 2026, updated 9 October

The publisher's direct library versions were rechecked against their primary
registries on 9 October 2026. The container follow-up updated Ruff to 0.17.0.
The initial 8 October split updated Handlebars from 4.7.9 to 4.7.10 and Playwright
from 1.63.0 to 1.64.0. Both npm lockfiles were refreshed for compatible transitive
updates.

| Dependency | Pin | Primary source |
| --- | --- | --- |
| jsonschema | 4.26.0 | [PyPI](https://pypi.org/pypi/jsonschema/json) |
| packaging | 26.3 | [PyPI](https://pypi.org/pypi/packaging/json) |
| Hatchling | 1.32.4 | [PyPI](https://pypi.org/pypi/hatchling/json) |
| build | 1.6.1 | [PyPI](https://pypi.org/pypi/build/json) |
| Ruff | 0.17.0 | [PyPI](https://pypi.org/pypi/ruff/json) |
| @eslint/js | 10.0.1 | [npm](https://registry.npmjs.org/@eslint/js/latest) |
| ESLint | 10.12.0 | [npm](https://registry.npmjs.org/eslint/latest) |
| globals | 17.13.0 | [npm](https://registry.npmjs.org/globals/latest) |
| HTML Validate | 11.16.2 | [npm](https://registry.npmjs.org/html-validate/latest) |
| Prettier | 3.9.9 | [npm](https://registry.npmjs.org/prettier/latest) |
| Stylelint | 17.16.0 | [npm](https://registry.npmjs.org/stylelint/latest) |
| stylelint-config-standard | 40.0.0 | [npm](https://registry.npmjs.org/stylelint-config-standard/latest) |
| @axe-core/playwright | 4.13.0 | [npm](https://registry.npmjs.org/@axe-core/playwright/latest) |
| GScan | 6.6.1 | [npm](https://registry.npmjs.org/gscan/latest) |
| Handlebars | 4.7.10 | [npm](https://registry.npmjs.org/handlebars/latest) |
| Playwright | 1.64.0 | [npm](https://registry.npmjs.org/playwright/latest) |

The toolchain uses Python 3.14.8 and npm 12.2.0. The Docker builder pins
[uv 0.12.24](https://github.com/astral-sh/uv/releases/tag/0.12.24) by version and
registry digest. The newest upstream Node release
is 26.11.1. GScan 6.6.1 declares Node `^22.17.0 || ^24.0.0`, so its isolated
runtime remains 24.21.0, the latest release in that supported line. The
[official Node release index](https://nodejs.org/dist/index.json) verifies both
versions. The host Node installation was not changed by this extraction.

Chrome for Testing is now 155.0.8059.39, the stable build in the
[official browser index](https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json).
`tools/ghost-theme/browser-version.json` records its official Linux x64 download
URL and SHA-256 `55672d1f392fd3e7b7a08621b6e804e6bcb39d40cf155504abb74b3a021ea8ea`.
The installer checked the downloaded archive and the executable's version.

Current upstream tools still constrain some transitive dependencies. GScan pins
its own Handlebars to 4.7.9 and Chalk to 6.0.0. The publisher's renderer uses
Handlebars 4.7.10. The latest axe Playwright integration constrains `axe-core` to
`~4.13.0`, excluding the newer 4.14.0 release. These are recorded compatibility
exceptions; the lock refresh respects upstream constraints. Other transitive
major versions can differ for the same reason. `npm outdated --all` reports
those constraints as well as missing optional peers, so its output needs review
before any override is introduced.

The publisher includes its own standalone, read-only dependency checker:

```sh
.venv/bin/python tools/check_dependencies.py --node-major 24
```

It reports both Node 26.11.1 and the selected supported 24.21.0 line. Installation
from the updated npm locks succeeded. GScan passed under its bundled Node
runtime, and a browser smoke test launched Chrome 155 through Playwright 1.64.0
and rendered a local document. Application and theme checks remain part of the
publisher's complete `make check` run.

The direct Python validation/build tools declare MIT or Apache/BSD licenses.
Most direct npm tools declare MIT; Playwright uses Apache-2.0 and
`@axe-core/playwright` uses MPL-2.0. Keep their upstream notices with redistributed
packages. Existing Humanizer instructions retain their pinned MIT license.
Theme, photo, geographic-data and external-service rights remain attached to
their own sources; making the publisher private does not replace those terms.

Future updates should query the primary registries, record exact direct pins,
refresh compatible locks, and run the complete checks. Keep network version
audits outside the offline test suite. The committed `uv.lock` records Python
transitives and build dependencies; `uv sync --locked --extra dev` recreates that
environment.
