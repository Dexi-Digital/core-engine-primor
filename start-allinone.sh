#!/usr/bin/env bash
# Sobe migrations + API + web no MESMO container (deploy de demo).
#
# Ordem importa: as migrations rodam ANTES de qualquer processo
# aceitar trafego. Se falharem, o container morre -- melhor do que
# subir com schema errado e quebrar no primeiro clique (foi o que
# aconteceu com `relation "auth_users" does not exist`).
set -euo pipefail

API_PORT=8000
WEB_PORT="${PORT:-3000}"

# O Next e a API nao podem disputar a mesma porta. `PORT=8000` era uma
# config valida quando so a API rodava aqui; agora a API e interna.
if [ "$WEB_PORT" = "$API_PORT" ]; then
  echo "[start] PORT=$API_PORT colide com a API interna; usando 3000 para a web." >&2
  WEB_PORT=3000
fi

echo "[start] aplicando migrations (alembic upgrade head)..."
alembic upgrade head
echo "[start] migrations OK"

# Seed opcional de demonstracao. Idempotente (upsert por chave
# natural), entao pode ficar ligado entre deploys sem duplicar.
if [ "${SEED_DEMO:-}" = "1" ] || [ "${SEED_DEMO:-}" = "true" ]; then
  echo "[start] SEED_DEMO ligado -- populando dados de demonstracao..."
  python -m scripts.seed_dossie
  echo "[start] seed OK"
fi

# Importacao do cadastro real da OnSafety. Opt-in e idempotente, mas
# traz DADO PESSOAL REAL quando o token e de producao -- por isso nunca
# roda sozinha.
if [ "${IMPORT_ONSAFETY:-}" = "1" ] || [ "${IMPORT_ONSAFETY:-}" = "true" ]; then
  echo "[start] IMPORT_ONSAFETY ligado -- importando cadastro de trabalhadores..."
  python -m scripts.import_trabalhadores_onsafety
  echo "[start] import OK"
fi

echo "[start] API em 127.0.0.1:${API_PORT}"
uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" &
api_pid=$!

echo "[start] web em 0.0.0.0:${WEB_PORT}"
cd /app/web
PORT="$WEB_PORT" HOSTNAME=0.0.0.0 node server.js &
web_pid=$!

# Se QUALQUER um dos dois morrer, derruba o container: o Railway
# reinicia e o estado degradado nao passa despercebido.
trap 'kill -TERM "$api_pid" "$web_pid" 2>/dev/null || true' TERM INT
wait -n "$api_pid" "$web_pid"
exit_code=$?
echo "[start] um dos processos saiu (code=$exit_code); encerrando o container." >&2
kill -TERM "$api_pid" "$web_pid" 2>/dev/null || true
exit "$exit_code"
