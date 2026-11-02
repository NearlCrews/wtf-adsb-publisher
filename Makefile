PYTHON ?= $(CURDIR)/.venv/bin/python
HOST_PYTHON ?= python3
export PATH := $(CURDIR)/.venv/bin:$(CURDIR)/tools/ghost-theme/node_modules/node/bin:$(PATH)
THEME_TOOLS = npm --silent --prefix tools/ghost-theme run
IMAGE ?= wtf-adsb-publisher:local
export CHROMIUM_PATH

.PHONY: lint lint-python lint-assets lint-deploy lint-theme theme-build test test-theme test-ghost check package docs test-wheel prepare-checks image test-image image-check

lint: lint-python lint-assets lint-deploy lint-theme

lint-python:
	ruff check src tools tests deploy/ghost-preview/*.py deploy/container/*.py
	ruff format --check src tools tests deploy/ghost-preview/*.py deploy/container/*.py

lint-assets:
	npm run lint

lint-deploy:
	shellcheck deploy/vps/*.sh deploy/ghost/*.sh
	$(PYTHON) tools/lint_deploy.py
	docker compose --profile jobs --profile maintenance --env-file deploy/container/.env.example -f deploy/container/compose.yaml config --no-env-resolution --quiet

theme-build:
	$(THEME_TOOLS) build

lint-theme: theme-build
	npm run --silent lint:theme-html
	$(THEME_TOOLS) check:ghost
	$(THEME_TOOLS) package

test:
	$(PYTHON) -B -m unittest discover -s tests -p 'test_*.py'

test-theme: theme-build
	$(THEME_TOOLS) browser:check
	$(THEME_TOOLS) check:replay
	$(THEME_TOOLS) check:media
	$(THEME_TOOLS) check:browser

test-ghost: lint-theme
	$(PYTHON) tools/ghost-theme/check_ghost.py

package:
	$(PYTHON) -m build --no-isolation

docs:
	$(PYTHON) tools/check_docs.py

test-wheel: package
	$(PYTHON) tools/check_wheel.py

prepare-checks:
	$(PYTHON) tools/ghost-theme/install_browser.py
	docker compose --env-file deploy/ghost/.env.example -f deploy/ghost/compose.yaml pull ghost caddy

check: lint docs test test-theme test-ghost test-wheel

image:
	docker build --tag "$(IMAGE)" .

test-image:
	$(HOST_PYTHON) tools/check_image.py --image "$(IMAGE)"

image-check: image
	$(MAKE) test-image IMAGE="$(IMAGE)"
