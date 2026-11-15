#!/bin/sh
# Start a local installation using Docker only; no host Python is needed.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
lan=false
browser=true
for argument in "$@"; do
  case "$argument" in
    --lan) lan=true ;;
    --no-browser) browser=false ;;
    --help|-h) printf 'Usage: sh scripts/start.sh [--lan] [--no-browser]\n'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$argument" >&2; exit 2 ;;
  esac
done
if ! command -v docker >/dev/null 2>&1; then
  printf 'Install Docker Desktop (Windows/macOS) or Docker Engine with Compose (Linux).\nSee README.md, then start this launcher again.\n' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose v2 is required. Update Docker Desktop or install the Docker Compose plugin.\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'Docker is not ready or your user cannot access it. Start Docker Desktop, or check the Linux Docker service and permissions.\n' >&2
  exit 1
fi
umask 077
mkdir -p data library
if [ ! -f .env ]; then
  app_uid=$(id -u)
  app_gid=$(id -g)
  if [ "$app_uid" -eq 0 ]; then
    app_uid=1000
    app_gid=1000
    chown "$app_uid:$app_gid" data library
  fi
  bind_ip=127.0.0.1
  if "$lan"; then bind_ip=0.0.0.0; fi
  printf 'APP_BIND_IP=%s\nAPP_PORT=1997\nAPP_UID=%s\nAPP_GID=%s\nLIBRARY_HOST_PATH=./library\nCOOKIE_SECURE=false\n' "$bind_ip" "$app_uid" "$app_gid" > .env
elif "$lan"; then
  temporary=$(mktemp .env.XXXXXX)
  awk '/^APP_BIND_IP=/{next} {print} END {print "APP_BIND_IP=0.0.0.0"}' .env > "$temporary"
  mv "$temporary" .env
fi
if [ "$(id -u)" -eq 0 ]; then
  configured_uid=$(awk -F= '/^APP_UID=/{gsub(/\r/,"",$2); value=$2} END {print value}' .env)
  configured_gid=$(awk -F= '/^APP_GID=/{gsub(/\r/,"",$2); value=$2} END {print value}' .env)
  configured_uid=${configured_uid:-1000}
  configured_gid=${configured_gid:-1000}
  case "$configured_uid:$configured_gid" in *[!0-9:]*|:*|*:) printf 'APP_UID and APP_GID must be numeric.\n' >&2; exit 1 ;; esac
  if [ "$configured_uid" -eq 0 ]; then printf 'Use a non-root APP_UID in .env.\n' >&2; exit 1; fi
  chown "$configured_uid:$configured_gid" data library
fi
printf 'Starting Kindle Sync. The first build downloads dependencies and may take several minutes.\n'
if ! docker compose up -d --build --wait --wait-timeout 240; then
  printf '\nStartup failed. Check Docker, your internet connection and whether the port is already in use.\nRun: docker compose logs --tail=50 app worker\nYour existing data has been kept.\n' >&2
  exit 1
fi
port=$(awk -F= '/^APP_PORT=/{gsub(/\r/,"",$2); print $2; exit}' .env)
port=${port:-1997}
case "$port" in *[!0-9]*) printf 'Invalid APP_PORT in .env\n' >&2; exit 1 ;; esac
url="http://localhost:$port"
printf '\nKindle Sync is ready: %s\nKeep Docker running while using the app.\n' "$url"
if "$lan"; then printf 'Local network access enabled. On another device use http://YOUR-COMPUTER-IP:%s on a trusted network.\n' "$port"; fi
if "$browser"; then
  if command -v open >/dev/null 2>&1; then open "$url" || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 || true
  fi
fi
