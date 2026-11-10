# AlphaForge AI service image (FastAPI + uvicorn over the ai_models package).
#
# The build context is code/, not code/ai_services/, because the service imports
# its sibling package ai_models. Build from the repository root with:
#
#   docker build -f code/ai_services/Dockerfile -t alphaforge-ai-services code
#
# Market data is not baked into the image. Mount a directory of <SYMBOL>.csv
# files at /data (read-only is fine). With AI_AUTO_TRAIN on (the default) the
# service trains missing or stale models at startup and does not accept requests
# until that finishes, so the first start with fresh data takes a little longer.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    ALPHAFORGE_DATA_DIR=/data \
    ALPHAFORGE_MODEL_DIR=/models \
    AI_HOST=0.0.0.0 \
    AI_PORT=8090

WORKDIR /app

# Dependencies first, so this layer is rebuilt only when a requirements file
# changes. ai_services/requirements.txt pulls in ../ai_models/requirements.txt,
# so both files are copied to the same relative locations they have in the repo.
# numpy, pandas and scikit-learn ship manylinux wheels, so no compiler is needed.
COPY ai_models/requirements.txt ai_models/requirements.txt
COPY ai_services/requirements.txt ai_services/requirements.txt
RUN pip install -r ai_services/requirements.txt

COPY ai_models/ ai_models/
COPY ai_services/ ai_services/

# Run unprivileged, with numeric IDs so Kubernetes' runAsNonRoot can verify it.
# /models is the only location the service writes to.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /models \
    && chown 10001:10001 /models
USER 10001:10001

EXPOSE 8090

# Generous start period: the first start may be training models (see above).
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/ai/health' % os.environ.get('AI_PORT', '8090'), timeout=4)"]

CMD ["python", "-m", "ai_services"]
