FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Single layer:
#   - git + ripgrep            (runtime: clone repos, scan code)
#   - nodejs + pnpm + yarn     (runtime: regenerate npm/yarn/pnpm lockfiles)
#   - poetry + pip-tools + pipenv (runtime: regenerate Python lockfiles)
#   - curl + gnupg             (build-only, purged after NodeSource setup)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        git ripgrep ca-certificates curl gnupg; \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    npm install --global --no-fund --no-audit pnpm@9 yarn@1; \
    npm cache clean --force; \
    pip install --no-cache-dir poetry pip-tools pipenv; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /root/.cache

WORKDIR /app

# Copy source BEFORE installing — hatchling reads scripts/patch_bot/ to build the wheel.
COPY pyproject.toml ./
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "patch_bot.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
