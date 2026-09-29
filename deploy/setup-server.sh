#!/usr/bin/env bash
# Разовая (идемпотентная) подготовка VPS. Запускается от root на сервере:
#   ssh <host> 'bash -s' < deploy/setup-server.sh
# Создаёт системного пользователя, каталоги, роль и базы PostgreSQL, shared/.env.
# Повторный запуск ничего не ломает и пароль не меняет.
set -euo pipefail

ROOT=/var/www/ai-client-responder
APP_USER=acr

id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$ROOT/releases" "$ROOT/shared" "$ROOT/backups"
chown "$APP_USER:$APP_USER" "$ROOT" "$ROOT/releases" "$ROOT/backups"
chown root:"$APP_USER" "$ROOT/shared" && chmod 750 "$ROOT/shared"

ENV_FILE="$ROOT/shared/.env"
if [ ! -f "$ENV_FILE" ]; then
  PW=$(openssl rand -hex 24)
  sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'acr') THEN
    CREATE ROLE acr LOGIN PASSWORD '$PW';
  ELSE
    ALTER ROLE acr PASSWORD '$PW';
  END IF;
END \$\$;
SQL
  umask 027
  cat > "$ENV_FILE" <<ENV
DATABASE_URL=postgresql+psycopg://acr:$PW@127.0.0.1:5432/acr
# Ollama — на ноутбуке владельца, приходит сюда обратным SSH-туннелем (см. README).
OLLAMA_URL=http://127.0.0.1:11434
LLM_MODEL=gemma3:4b
LLM_TIMEOUT_S=240
ENV
  chown root:"$APP_USER" "$ENV_FILE" && chmod 640 "$ENV_FILE"
  echo "created $ENV_FILE"
fi

# acr — боевая база, acr_dev — для локальной разработки через SSH-туннель.
for db in acr acr_dev; do
  if ! sudo -u postgres psql -Atc "SELECT 1 FROM pg_database WHERE datname = '$db'" | grep -q 1; then
    sudo -u postgres createdb -O acr "$db"
    echo "created database $db"
  fi
done

# systemd-юнит — из текущего релиза (если он уже есть).
if [ -f "$ROOT/current/deploy/acr.service" ]; then
  install -m 644 "$ROOT/current/deploy/acr.service" /etc/systemd/system/acr.service
  systemctl daemon-reload
  systemctl enable acr >/dev/null 2>&1 || true
fi
echo "setup ok"
