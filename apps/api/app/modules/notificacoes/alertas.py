"""Caminho unico de despacho dos alertas de vencimento.

Usado por certidoes (D.6), ASO (A.2), afastamentos INSS (D4) e
contratos (Squad 5). Regras:

1. SEMPRE cria uma `Notificacao` por destinatario. E isso que faz o
   alerta valer: nao depende de nenhum servico externo, entao funciona
   hoje, sem a permissao de e-mail da Primor.
2. ADICIONALMENTE manda e-mail pelo Microsoft 365 (Graph) quando o
   envio esta configurado (`mailer` nao-None).
3. Falha de e-mail NAO derruba o alerta: a notificacao ja existe, o
   erro e logado e devolvido em `email_status`/`email_error` para o
   chamador gravar no log do fluxo.

Idempotencia: cada fluxo continua marcando "ja alertado" na sua propria
tabela de log (status `sent`). Aqui a `chave_idempotencia` da
notificacao so garante que um reprocessamento (ex.: commit do log
falhou depois das notificacoes) nao duplique o aviso no sino.

Falha ao criar notificacao (erro de banco) SOBE -- o chamador trata
como `failed`, e o proximo cron tenta de novo.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.msgraph_mail.client import GraphMailClient
from app.modules.notificacoes.service import criar_notificacao

logger = logging.getLogger(__name__)

EMAIL_ENVIADO = "enviado"
EMAIL_FALHOU = "falhou"
EMAIL_NAO_CONFIGURADO = "nao_configurado"


@dataclass(slots=True)
class ResultadoDespacho:
    notificacoes: int
    email_status: str  # enviado | falhou | nao_configurado
    email_error: str | None = None


async def despachar_alerta(
    db: AsyncSession,
    *,
    recipients: Sequence[str],
    categoria: str,
    titulo: str,
    corpo: str,
    link: str | None,
    chave_idempotencia: str,
    email_subject: str,
    email_html: str,
    mailer: GraphMailClient | None,
) -> ResultadoDespacho:
    destinatarios = [r.strip() for r in recipients if r and r.strip()]

    for destinatario in destinatarios:
        await criar_notificacao(
            db,
            destinatario=destinatario,
            titulo=titulo,
            corpo=corpo,
            categoria=categoria,
            link=link,
            chave_idempotencia=chave_idempotencia,
        )

    if mailer is None:
        return ResultadoDespacho(
            notificacoes=len(destinatarios), email_status=EMAIL_NAO_CONFIGURADO
        )

    try:
        await mailer.send_mail(to=destinatarios, subject=email_subject, html=email_html)
    except Exception as exc:  # noqa: BLE001 -- e-mail e canal extra, nunca derruba o alerta
        erro = str(exc)[:1024]
        logger.warning(
            "alerta %s: notificacao criada, e-mail falhou: %s",
            chave_idempotencia,
            erro,
        )
        return ResultadoDespacho(
            notificacoes=len(destinatarios),
            email_status=EMAIL_FALHOU,
            email_error=erro,
        )

    return ResultadoDespacho(
        notificacoes=len(destinatarios), email_status=EMAIL_ENVIADO
    )


__all__ = [
    "EMAIL_ENVIADO",
    "EMAIL_FALHOU",
    "EMAIL_NAO_CONFIGURADO",
    "ResultadoDespacho",
    "despachar_alerta",
]
