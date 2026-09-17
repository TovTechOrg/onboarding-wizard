FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Created early so USER can drop to it below. Deliberately never
# `chown -R`'d to it: nothing under /app is ever written to at runtime, so
# appuser only ever needs the read+execute permissions COPY/RUN already
# leave in place by default. A chown in its own layer would duplicate the
# entire venv + app code into a new layer (overlayfs stores a changed file
# as a full copy, not a diff) -- measured at +110MB/+29% image size on the
# sibling review-engine project's own Dockerfile for zero functional
# benefit; same shape here.
RUN useradd -m -u 1000 appuser

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Explicit, not `COPY . .`: the blanket form made .dockerignore the only gate
# on what ships (it was shipping .env.example and local agent config), and
# would ship demo/ into production -- which .dockerignore cannot prevent,
# since it applies to Dockerfile.demo's build too.
COPY main.py router.py config.py session_store.py ./
COPY render_client.py github_client.py llm_client.py ./
COPY supabase_client.py uptimerobot_client.py ./
COPY static/ ./static/
COPY contracts/ ./contracts/

USER appuser

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "--no-dev", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
