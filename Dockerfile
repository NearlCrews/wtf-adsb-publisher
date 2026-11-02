FROM python:3.15.0-slim@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58 AS python-base

FROM python-base AS node
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
# Official Node and npm archives, with their verified upstream checksums.
RUN curl --fail --location --retry 3 --output /tmp/node.tar.xz https://nodejs.org/dist/v24.22.1/node-v24.22.1-linux-x64.tar.xz \
    && echo '6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6  /tmp/node.tar.xz' | sha256sum --check - \
    && mkdir /opt/node \
    && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1 \
    && rm /tmp/node.tar.xz
RUN curl --fail --location --retry 3 --output /tmp/npm.tgz https://registry.npmjs.org/npm/-/npm-12.2.0.tgz \
    && echo '66c2632a94e796649738b2d7894d710c2cc2e1696893823066687f6b0d8a52e5f2b54eeaeaa32ffdc513ecca1e49ff794a5c2ec3f9515d879f1b3182b291cf35  /tmp/npm.tgz' | sha512sum --check - \
    && rm -rf /opt/node/lib/node_modules/npm \
    && mkdir /opt/node/lib/node_modules/npm \
    && tar -xzf /tmp/npm.tgz -C /opt/node/lib/node_modules/npm --strip-components=1 \
    && rm /tmp/npm.tgz

FROM python-base AS runtime-base
# Chrome's shared libraries and SSH are runtime requirements. Application fonts
# are copied with their notices; system fonts cover fallback characters.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates openssh-client fonts-liberation fonts-noto-color-emoji \
       libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
       libcairo2 libcups2t64 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 \
       libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
       libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /opt/node /opt/node
ENV PATH="/opt/venv/bin:/opt/node/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    HOME=/home/publisher \
    XDG_CONFIG_HOME=/tmp/chromium-config XDG_CACHE_HOME=/tmp/chromium-cache

FROM runtime-base AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN UV_PYTHON_DOWNLOADS=never uv sync --locked --extra dev --no-install-project --python /usr/local/bin/python
COPY tools/ghost-theme/package.json tools/ghost-theme/package-lock.json tools/ghost-theme/install_browser.py tools/ghost-theme/browser-version.json ./tools/ghost-theme/
RUN npm --prefix tools/ghost-theme ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && python tools/ghost-theme/install_browser.py
COPY . .
RUN uv build --wheel --no-build-isolation --out-dir /tmp/wheels \
    && uv export --quiet --locked --no-dev --no-emit-project --format requirements-txt --output-file /tmp/runtime-requirements.txt \
    && python -m venv /opt/venv \
    && uv pip sync --python /opt/venv/bin/python --require-hashes /tmp/runtime-requirements.txt \
    && uv pip install --python /opt/venv/bin/python --no-deps /tmp/wheels/*.whl \
    && uv pip check --python /opt/venv/bin/python \
    && rm -rf /app/.venv \
    && chmod -R a+rX,go-w /app /opt/venv \
    && python deploy/container/build_metadata.py

FROM runtime-base AS publisher
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
COPY --from=build /usr/local/share/wtf-adsb-publisher /usr/local/share/wtf-adsb-publisher
RUN groupadd --gid 1000 publisher \
    && useradd --uid 1000 --gid publisher --create-home --home-dir /home/publisher --shell /bin/sh publisher \
    && install -d -o publisher -g publisher -m 0700 /home/publisher/.ssh /app/artifacts/ghost-theme
USER publisher:publisher
ENTRYPOINT ["python", "/app/deploy/container/entrypoint.py"]
CMD ["--help"]
