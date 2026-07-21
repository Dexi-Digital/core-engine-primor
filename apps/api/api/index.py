"""Entrypoint serverless para a Vercel (Python runtime, `@vercel/python`).

A Vercel exige um arquivo sob `api/` que exporte um ASGI callable
chamado `app`. O FastAPI real vive em `app/main.py` -- este modulo
so re-exporta, sem duplicar logica. Nao usado fora da Vercel (dev local
e Docker seguem chamando `app.main:app` via uvicorn direto).
"""
from app.main import app  # noqa: F401
