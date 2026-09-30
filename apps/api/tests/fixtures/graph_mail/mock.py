"""GraphMailClient apontando para um httpx.MockTransport (sem rede).

Responde o token do Entra e o `sendMail` do Graph. `status` controla a
resposta do sendMail (202 = aceito; 403 = permissao Mail.Send ausente).
Cada request de sendMail e guardado em `captured` para os asserts.
"""
from __future__ import annotations

import httpx

from app.integrations.msgraph_mail.client import GraphMailClient

SENDER = "sistemas@primor.example"


def mock_graph_mailer(
    captured: list[httpx.Request], *, status: int = 202
) -> GraphMailClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(
                200, json={"access_token": "tok-test", "expires_in": 3600}
            )
        captured.append(request)
        if status == 202:
            return httpx.Response(202)
        return httpx.Response(
            status,
            json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}},
        )

    return GraphMailClient(
        tenant_id="tenant-test",
        client_id="client-test",
        client_secret="secret-test",
        sender=SENDER,
        transport=httpx.MockTransport(handler),
    )
