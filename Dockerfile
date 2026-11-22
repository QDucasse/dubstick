FROM python:3.12.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

# ffmpeg decodes every audio format for the analysis; Liberation Sans Narrow draws the labels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3.12 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# Same uid as the host user, so the read-only music mounts and the data volume line up.
RUN useradd --uid 1000 --create-home dubstick \
    && mkdir -p /data \
    && chown dubstick:dubstick /data
USER dubstick

ENV DUBSTICK_DATA=/data \
    DUBSTICK_MUSIC=/music \
    DUBSTICK_PORT=8000

EXPOSE 8000
CMD ["dubstick"]
