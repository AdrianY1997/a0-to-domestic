# syntax=docker/dockerfile:1
# a0-to-domestic image (SPEC 12.1). Targets: slim, dev, full (default = last stage).
# Build: docker build -f docker/Dockerfile --target full -t a0-to-domestic:latest .

ARG PYTHON_IMAGE=python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

# ---------------------------------------------------------------- frontend-build
FROM node:24-slim AS frontend-build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- libredwg-build
FROM ${PYTHON_IMAGE} AS libredwg-build
ARG LIBREDWG_VERSION=0.13.3
ARG LIBREDWG_SHA256=83f1f6e78a744777a481ff4520e4cef3f8ac4b2c1c25671077ca12fe81e8816e
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl xz-utils \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL -o libredwg.tar.xz \
      "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.xz" \
 && echo "${LIBREDWG_SHA256}  libredwg.tar.xz" | sha256sum -c - \
 && tar -xJf libredwg.tar.xz --strip-components=1 \
 && ./configure --prefix=/opt/libredwg --disable-static --disable-bindings --disable-werror \
      --disable-docs --disable-dependency-tracking \
 && make -j"$(nproc)" \
 && make install \
 && strip --strip-unneeded /opt/libredwg/bin/* /opt/libredwg/lib/*.so* || true
RUN /opt/libredwg/bin/dwg2dxf --version

# ---------------------------------------------------------------- base
FROM ${PYTHON_IMAGE} AS base
ARG A0TD_VERSION=0.1.0
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH

# libvips (pyvips has no bundled binary), tini, basic fonts. Names checked against Debian trixie
# (libvips42t64 is the t64-renamed runtime library).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tini ca-certificates libvips42t64 \
      fonts-dejavu-core fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp/home --shell /usr/sbin/nologin app \
 && mkdir -p /app/backend /data/tmp \
 && chown -R app:app /data

WORKDIR /app/backend
# Dependencies only (layer cached until the lockfile changes).
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

ENV HOME=/tmp/home \
    XDG_CACHE_HOME=/tmp/cache \
    TMPDIR=/data/tmp \
    A0TD_DATA_DIR=/data \
    A0TD_TMP_DIR=/data/tmp \
    A0TD_PORT=8080 \
    A0TD_STATIC_DIR=/app/static \
    A0TD_VERSION=${A0TD_VERSION}

# ---------------------------------------------------------------- slim
FROM base AS slim
# Ghostscript, qpdf, librsvg (rsvg-convert)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ghostscript qpdf librsvg2-bin \
 && rm -rf /var/lib/apt/lists/*
COPY backend/ /app/backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable
COPY --from=frontend-build /src/dist /app/static
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod 0755 /app/entrypoint.sh
USER 10001:10001
EXPOSE 8080
VOLUME /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('A0TD_PORT','8080'), timeout=4).status == 200 else 1)"]
ENTRYPOINT ["tini", "--", "/app/entrypoint.sh"]

# ---------------------------------------------------------------- full-tools (shared by dev and full)
FROM slim AS full-tools
USER root
# LibreOffice (no Java, no recommends), Inkscape, DjVuLibre. The python3-* packages are what Inkscape's
# Python extensions (inkex, HPGL import) need; Inkscape only *recommends* them (WP-17).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw \
      inkscape djvulibre-bin \
      python3-lxml python3-numpy python3-pyparsing python3-tinycss2 python3-cssselect \
 && rm -rf /var/lib/apt/lists/*
# LibreDWG: dwg2dxf + shared library
COPY --from=libredwg-build /opt/libredwg /opt/libredwg
RUN echo /opt/libredwg/lib > /etc/ld.so.conf.d/libredwg.conf && ldconfig \
 && ln -s /opt/libredwg/bin/dwg2dxf /usr/local/bin/dwg2dxf \
 && ln -s /opt/libredwg/bin/dxf2dwg /usr/local/bin/dxf2dwg
# LibreOffice with macros disabled: site-wide default (Debian reads /etc/libreoffice) plus a template
# for the per-job profiles (copy into <UserInstallation>/user/registrymodifications.xcu).
COPY docker/libreoffice/registrymodifications.xcu /usr/share/a0td/registrymodifications.xcu
RUN mkdir -p /etc/libreoffice  && install -m 0644 /usr/share/a0td/registrymodifications.xcu /etc/libreoffice/registrymodifications.xcu
USER 10001:10001

# ---------------------------------------------------------------- dev
FROM full-tools AS dev
USER root
# Node 24 (frontend tooling inside the dev container)
COPY --from=node:24-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
# Dev group (pytest, ruff, mypy...) and an editable install; ./backend is bind-mounted over
# /app/backend at runtime, the venv lives outside the mount in /opt/venv.
ENV UV_COMPILE_BYTECODE=0
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --group dev \
 && chown -R app:app /opt/venv
USER 10001:10001
CMD ["uvicorn", "a0td.api.app:create_app", "--factory", "--reload", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- full (default, last stage)
FROM full-tools AS full
