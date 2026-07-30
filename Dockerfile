# syntax=docker/dockerfile:1.7

FROM docker:27.5.1-cli-alpine3.21 AS docker-cli

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/eventsignal/backend \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    SPIDERFORGE_DATA_DIR=/data/spider_forge

WORKDIR /opt/eventsignal/backend

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY backend/app/spider_forge_system/requirements.txt /tmp/spider-forge-requirements.txt

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/spider-forge-requirements.txt \
    && python -m playwright install --with-deps chromium \
    && mkdir -p /data/spider_forge /ms-playwright

COPY backend/app/__init__.py /opt/eventsignal/backend/app/__init__.py
COPY backend/app/spider_forge_system /opt/eventsignal/backend/app/spider_forge_system

ENTRYPOINT ["python", "-m", "app.spider_forge_system"]
CMD ["run", "--file", "/data/spider_forge/requests/urls.txt"]
