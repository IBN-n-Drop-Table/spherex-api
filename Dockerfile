# SPHEREx API: FastAPI + science stack for live jobs.
# Sized for free tiers (~512 MB RAM, ephemeral disk). Measured locally with
# `podman run --memory=512m` (see README, "Deploy").
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=8000 \
    LIVE_DOWNLOAD_WORKERS=4 \
    LIVE_MAX_PER_DETECTOR=1 \
    LIVE_MAX_RUNNING_JOBS=1 \
    LIVE_MAX_FOV_ARCMIN=15 \
    SIA_BUDGET_S=5

WORKDIR /srv
COPY requirements.txt requirements-science.txt ./
RUN pip install -r requirements-science.txt

RUN useradd --create-home --home-dir /home/app app
COPY app ./app
COPY scripts ./scripts
RUN chown -R app:app /srv/app/static
USER app

EXPOSE 8000
# One worker on purpose: jobs live in this process's memory, and one live
# job at a time keeps RAM bounded. --proxy-headers so URLs come out https.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
