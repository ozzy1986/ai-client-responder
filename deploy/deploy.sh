#!/usr/bin/env bash
# Релиз на VPS (запускать из корня репозитория в Git Bash / Linux):
#   deploy/deploy.sh             — тесты → загрузка релиза → venv → миграции → seed → переключение → healthcheck
#   deploy/deploy.sh --rollback  — вернуть предыдущий релиз
# Релизы: releases/<метка>, у каждого свой venv; боевой — симлинк current (как в CRM).
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG=deploy/.env
[ -f "$CONFIG" ] && [ -z "${ACR_DEPLOY_HOST:-}" ] && . "$CONFIG"
HOST=${ACR_DEPLOY_HOST:?нет ACR_DEPLOY_HOST: скопируйте deploy/.env.example в deploy/.env}
ROOT=/var/www/ai-client-responder
AS_APP="sudo -u acr bash -c"
ENV_RUN="set -a; . $ROOT/shared/.env; set +a; export ACR_ENV_FILE=/nonexistent PYTHONDONTWRITEBYTECODE=1;"

healthcheck() {
  ssh "$HOST" 'for i in $(seq 1 15); do
      curl -sf --max-time 3 http://127.0.0.1:8102/api/healthz | grep -q ok && { echo healthy; exit 0; }
      sleep 1
    done; echo UNHEALTHY; journalctl -u acr -n 30 --no-pager; exit 1'
}

switch_to() {
  ssh "$HOST" "ln -sfn $1 $ROOT/current.tmp && mv -Tf $ROOT/current.tmp $ROOT/current \
    && bash $ROOT/current/deploy/setup-server.sh >/dev/null && systemctl restart acr"
}

if [ "${1:-}" = "--rollback" ]; then
  CURRENT=$(ssh "$HOST" "readlink $ROOT/current")
  PREV=$(ssh "$HOST" "ls -1d $ROOT/releases/* | sort | awk -v cur='$CURRENT' '\$0 < cur' | tail -1")
  [ -n "$PREV" ] || { echo "нет релиза старше $CURRENT"; exit 1; }
  switch_to "$PREV" && healthcheck && echo "rolled back to $PREV"
  exit 0
fi

if [ "${SKIP_TESTS:-0}" != "1" ]; then
  echo '--- unit tests ---'
  PY=backend/.venv/Scripts/python; [ -x "$PY" ] || PY=backend/.venv/bin/python
  (cd backend && "../$PY" -m pytest -q -p no:cacheprovider)
fi

TS=$(date +%Y%m%d-%H%M%S)
REL=$ROOT/releases/$TS
VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo dev)

echo "--- upload release $TS ($VERSION) ---"
ssh "$HOST" 'bash -s' < deploy/setup-server.sh >/dev/null
tar czf - --exclude=backend/.venv --exclude='backend/.env*' --exclude=__pycache__ \
  --exclude=.pytest_cache --exclude=.ruff_cache backend data frontend deploy \
  | ssh "$HOST" "mkdir -p $REL && tar xzf - -C $REL && echo $VERSION > $REL/VERSION \
      && chown -R acr:acr $REL && chmod -R o+rX $REL"

echo '--- venv ---'
ssh "$HOST" "$AS_APP 'python3 -m venv $REL/venv && $REL/venv/bin/pip install -q --upgrade pip \
  && $REL/venv/bin/pip install -q -r $REL/backend/requirements.txt'"

echo '--- db dump, migrations, seed ---'
ssh "$HOST" "$AS_APP '$ENV_RUN cd $REL/backend \
  && pg_dump -Fc \"\${DATABASE_URL/+psycopg/}\" > $ROOT/backups/pre-$TS.dump \
  && ls -1t $ROOT/backups/pre-*.dump | tail -n +6 | xargs -r rm -f \
  && $REL/venv/bin/alembic upgrade head && $REL/venv/bin/python -m app.seed'"

PREV=$(ssh "$HOST" "readlink $ROOT/current 2>/dev/null || true")
echo "--- switch current → $TS (prev: ${PREV:-none}) ---"
if switch_to "$REL" && healthcheck; then
  echo "deployed $VERSION as $TS"
  ssh "$HOST" "ls -1d $ROOT/releases/* | sort | head -n -3 | xargs -r rm -rf"
else
  [ -n "$PREV" ] && { echo "!!! rollback to $PREV"; switch_to "$PREV" && healthcheck; }
  exit 1
fi
