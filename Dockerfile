# Harness platform service image.
FROM python:3.12-slim AS base

# Agents run as OS processes spawned by this same container (ProcessRuntime),
# so the image needs a plain Python 3 available on PATH for the example
# code-agent's entrypoint (["python3", "agent.py", ...]) to work as-is. A
# real deployment running code agents via ContainerRuntime instead would not
# need this, since those run in their own separate images.
RUN groupadd -r harness && useradd -r -g harness -m harness

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY harness ./harness
COPY policies ./policies
COPY demo ./demo

RUN mkdir -p /app/data /tmp/harness-sandboxes && chown -R harness:harness /app/data /tmp/harness-sandboxes

USER harness

ENV HARNESS_DATABASE_URL=sqlite+aiosqlite:////app/data/harness.db \
    HARNESS_RUNTIME_WORKDIR_ROOT=/tmp/harness-sandboxes \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

CMD ["uvicorn", "harness.main:app", "--host", "0.0.0.0", "--port", "8000"]
