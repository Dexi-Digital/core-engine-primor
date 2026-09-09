"""Adapter TOTVS RM -- Modulo C (ERP financeiro).

READ-ONLY POR CONSTRUCAO (decisao #1). Nao existe metodo de escrita
aqui: nao e flag, nao e default, o metodo nao existe. `push` esta
sobrescrito so para falhar alto e explicar o porque -- herdar o
`NotImplementedError` generico da base esconderia a intencao. Perfil
somente-leitura no RM e a outra camada; se uma falhar, a outra segura.

A extracao fica atras de `TotvsExtractor` (REST ou wsConsultaSQL) --
ver `extractor.py` para o racional e o que ainda depende da TOTVS.

O pull roda SO no worker, sequencial, sob lock single-flight
(`app.core.locks`). Nunca na API: licenca de WebService do RM e
consumida por requisicao e concorrencia aqui e dinheiro e risco de
travar o ERP da operacao.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.integrations.base import IntegrationClient
from app.integrations.totvs.extractor import MockExtractor, TotvsExtractor

logger = logging.getLogger(__name__)


class TotvsReadOnlyError(RuntimeError):
    """Tentativa de escrever no RM atraves deste adapter."""


class TotvsClient(IntegrationClient):
    name = "totvs"

    def __init__(self, *, extractor: TotvsExtractor | None = None) -> None:
        self._extractor: TotvsExtractor = extractor or MockExtractor()

    @property
    def source(self) -> str:
        """`totvs_rest` | `totvs_consultasql` | `totvs_mock`."""
        return self._extractor.source

    async def health_check(self) -> bool:
        """False ate credencial e porta confirmadas (decisao #9).

        Com o extractor REST, bate em `/api/framework/v1/coligadas`, que
        nao consome licenca nessa versao. Com mock, e sempre False.
        """
        return await self._extractor.health_check()

    async def fetch_lancamentos(
        self, *, desde: date, ate: date
    ) -> list[dict[str, Any]]:
        """Lancamentos financeiros da janela. Unico ponto de leitura."""
        linhas = await self._extractor.fetch_lancamentos(desde=desde, ate=ate)
        logger.info(
            "totvs.fetch_lancamentos source=%s janela=%s..%s linhas=%d",
            self.source,
            desde,
            ate,
            len(linhas),
        )
        return linhas

    async def list_coligadas(self) -> set[int] | None:
        """Coligadas que o perfil do usuario enxerga, ou None se o
        extractor nao souber listar.

        Usa `/api/framework/v1/coligadas`, que NAO consome licenca
        nessa versao -- por isso da para chamar em toda execucao do
        pull sem custo.
        """
        listar = getattr(self._extractor, "list_coligadas", None)
        if listar is None:
            return None
        return await listar()

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        return await self.fetch_lancamentos(*args, **kwargs)

    async def push(self, *args: Any, **kwargs: Any) -> Any:
        raise TotvsReadOnlyError(
            "adapter TOTVS e read-only por construcao (decisao #1): nao ha "
            "caminho de escrita no RM. Ver docs/integrations.md."
        )

    async def aclose(self) -> None:
        await self._extractor.aclose()


def build_totvs_client() -> TotvsClient:
    """Monta o client a partir das settings.

    Sem `TOTVS_USERNAME`/`TOTVS_PASSWORD`, cai no mock deterministico --
    mesmo padrao de Dominio, Onvio, Tangerino, OnSafety e DirectData.
    Lembre que o mock tem `health_check` False de proposito: ele
    destrava dev/CI, nao pinta o painel de verde (decisao #9).
    """
    from app.core.config import get_settings
    from app.integrations.totvs.extractor import (
        ConsultaSqlExtractor,
        RestExtractor,
    )

    s = get_settings()
    if not (s.totvs_base_url and s.totvs_username and s.totvs_password):
        logger.info("totvs: credencial ausente, usando mock deterministico")
        return TotvsClient(extractor=MockExtractor())

    modo = (s.totvs_extractor or "").strip().lower()
    comum = {
        "base_url": s.totvs_base_url,
        "username": s.totvs_username,
        "password": s.totvs_password,
    }

    if modo == "rest":
        return TotvsClient(
            extractor=RestExtractor(lancamentos_path=s.totvs_lancamentos_path, **comum)
        )
    if modo == "consultasql":
        if not s.totvs_consultasql_cod_sentenca:
            raise ValueError(
                "TOTVS_CONSULTASQL_COD_SENTENCA e obrigatorio quando "
                "TOTVS_EXTRACTOR=consultasql -- a sentenca e cadastrada "
                "dentro do RM (BI > Criacao de consultas SQL)."
            )
        return TotvsClient(
            extractor=ConsultaSqlExtractor(
                cod_sentenca=s.totvs_consultasql_cod_sentenca,
                cod_coligada=s.totvs_consultasql_cod_coligada,
                cod_sistema=s.totvs_consultasql_cod_sistema,
                **comum,
            )
        )
    if modo == "mock":
        return TotvsClient(extractor=MockExtractor())
    raise ValueError(
        f"TOTVS_EXTRACTOR invalido: {s.totvs_extractor!r} "
        "(esperado: rest | consultasql | mock)"
    )
