"""E-mail via Microsoft 365 (Graph sendMail)."""
from app.integrations.msgraph_mail.client import (
    GraphMailClient,
    GraphMailError,
    GraphMailPermissaoNegada,
    abrir_mail_client,
    build_mail_client,
    mail_configurado,
)

__all__ = [
    "GraphMailClient",
    "GraphMailError",
    "GraphMailPermissaoNegada",
    "abrir_mail_client",
    "build_mail_client",
    "mail_configurado",
]
