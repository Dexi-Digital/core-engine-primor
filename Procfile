# Procfile -- usado por Render, Railway, Heroku-likes e Fly.io
# (via fly.toml `[deploy] release_command`). Os comandos sao a fonte
# de verdade dos processos da Option A; veja docs/deployment-option-a.md.
release: cd apps/api && alembic upgrade head
web: cd apps/api && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: cd apps/workers && celery -A worker.main.celery_app worker -B -s /tmp/celerybeat-schedule --loglevel=info -Q default,dp_sesmt,manutencao,financeiro,licitacoes,ia
