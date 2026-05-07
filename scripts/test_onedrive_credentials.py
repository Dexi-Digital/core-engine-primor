"""Smoke test das credenciais Microsoft Graph / OneDrive.

Uso:
    cd apps/api
    MS_GRAPH_TENANT_ID=... MS_GRAPH_CLIENT_ID=... \
    MS_GRAPH_CLIENT_SECRET=... MS_GRAPH_DRIVE_ID=... \
    python ../../scripts/test_onedrive_credentials.py

Valida em ~10s, sem subir API:
  1. Token client_credentials e emitido
  2. Drive configurado responde GET /drives/{id}/root
  3. Upload de payload pequeno (~50 bytes) funciona
  4. Download retorna o mesmo payload
  5. Delete limpa o objeto teste

Saida: rc=0 -> tudo OK; rc=1 -> alguma etapa quebrou (ja loga o motivo).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.integrations.onedrive.client import (  # noqa: E402
    OneDriveClient,
    OneDriveError,
)


async def main() -> int:
    required = [
        "MS_GRAPH_TENANT_ID",
        "MS_GRAPH_CLIENT_ID",
        "MS_GRAPH_CLIENT_SECRET",
        "MS_GRAPH_DRIVE_ID",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"FAIL: variaveis ausentes: {', '.join(missing)}")
        return 1

    client = OneDriveClient(
        tenant_id=os.environ["MS_GRAPH_TENANT_ID"],
        client_id=os.environ["MS_GRAPH_CLIENT_ID"],
        client_secret=os.environ["MS_GRAPH_CLIENT_SECRET"],
        drive_id=os.environ["MS_GRAPH_DRIVE_ID"],
        root_folder=os.environ.get("MS_GRAPH_ROOT_FOLDER", "MotorCentral/smoke-test"),
    )

    try:
        print("[1/5] Validando token client_credentials + drive root...")
        ok = await client.health_check()
        if not ok:
            print("FAIL: health_check retornou False (token ou drive_id invalidos)")
            return 1
        print("       OK")

        payload = b"motor-central-smoke-test-payload\n"
        relative_path = "smoke-test.txt"

        async def _stream() -> "asyncio.AsyncIterator[bytes]":
            yield payload

        print("[2/5] Upload de smoke-test.txt...")
        item = await client.upload(relative_path=relative_path, content=_stream())
        item_id = item["id"]
        print(f"       OK (id={item_id})")

        print("[3/5] Download do mesmo objeto...")
        got = await client.download(item_id)
        if got != payload:
            print(
                f"FAIL: download retornou {len(got)} bytes diferentes "
                f"(esperava {len(payload)})"
            )
            return 1
        print(f"       OK ({len(got)} bytes batem)")

        print("[4/5] Listar root_folder pra confirmar item visivel...")
        items = await client.list_folder(relative_path="")
        names = [it.get("name") for it in items]
        if "smoke-test.txt" not in names:
            print(f"FAIL: item nao listado em {client.root_folder}: {names}")
            return 1
        print(f"       OK ({len(items)} item(ns) em {client.root_folder})")

        print("[5/5] Delete...")
        await client.delete(item_id)
        print("       OK")

        print("\nSmoke test PASSOU. Credenciais MS_GRAPH_* prontas para uso.")
        return 0
    except OneDriveError as exc:
        print(f"FAIL OneDriveError: {exc}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
