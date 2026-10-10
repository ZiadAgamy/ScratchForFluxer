# The runner of Scratch For Fluxer: the service that keeps people's bots connected to Fluxer.
#
# Build it from the project folder (the one with requirements.txt):
#     docker build -f deploy/runner/Dockerfile -t sff-runner .
#
# How to run it (it has to be started as root, with a few Linux capabilities, so that every bot can get a user of its
# own) is in deploy/runner/README.md. deploy/runner/docker-compose.yml has all of it written down.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    RUNNER_DATA_DIR=/var/sff/runs \
    RUNNER_UID_BASE=41000

# tini collects the processes a bot leaves behind, so they don't pile up as zombies ("docker run --init" does the same).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY scratch_fluxer ./scratch_fluxer

# Where the bots' folders live. Better to mount a small tmpfs here (see the README): a bot can then never fill the disk.
RUN mkdir -p /var/sff/runs && chmod 700 /var/sff/runs

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8080'), timeout=4)"]

# One worker, on purpose: the bots that are running belong to this one process.
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "exec uvicorn scratch_fluxer.runner.app:create_runner_app --factory --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-access-log"]
