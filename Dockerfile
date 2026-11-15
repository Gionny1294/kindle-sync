FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends calibre antiword && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data LIBRARY_DIR=/library KINDLE_SYNC_CONFIG=/data/kindle-sync.conf KINDLE_SYNC_STATE_DIR=/data
ARG APP_UID=1000
ARG APP_GID=1000
WORKDIR /opt/kindle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && (getent group ${APP_GID} >/dev/null || groupadd -g ${APP_GID} kindle) && (getent passwd ${APP_UID} >/dev/null || useradd -u ${APP_UID} -g ${APP_GID} -M kindle)
COPY --chmod=644 kindle-sync.py ./
COPY app ./app
USER ${APP_UID}:${APP_GID}
CMD ["gunicorn", "--bind", "0.0.0.0:1997", "--workers", "1", "--threads", "4", "--timeout", "180", "--access-logfile", "/dev/null", "app.web:app"]
