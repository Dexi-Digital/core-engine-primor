"""Infosimples -- consultas Detran (multas, IPVA, licenciamento, debitos).

A Infosimples (https://infosimples.com) agrega consultas em ~26 Detrans
estaduais e expoe REST/JSON com auth por token. Usamos especificamente
os endpoints `/api/v2/consultas/detran/<uf>/veiculo` para SP/MG/GO.

Padrao do projeto: sem chave (`INFOSIMPLES_TOKEN` vazio), o adapter
cai num mock deterministico para nao bloquear dev/CI -- mesmo padrao
de DirectData (M\u00f3dulo A), LLM (D.5), OneDrive (#11), Dominio (#12).

Por que Infosimples e nao Playwright direto nos portais Detran?
  - Detran SP/MG/GO exigem credencial de despachante credenciado para
    consulta detalhada. A Primor nao tem despachante interno -- pagar
    por consulta via agregador sai mais barato e mais confiavel.
  - Sem captcha solver, sem manutencao de Playwright contra mudancas
    de DOM (as paginas dos Detrans mudam bastante), sem risco de IP
    block. RPA via Playwright fica como caminho alternativo se a
    Primor algum dia contratar despachante.

Resposta normalizada (mesma para SP/MG/GO):
  {
    "uf": "SP",
    "placa": "ABC1234",
    "renavam": "12345678900" | None,
    "chassi": "..." | None,
    "marca_modelo": "VW/CONSTELLATION 24.280",
    "ano_modelo": 2021,
    "cor": "BRANCA",
    "combustivel": "DIESEL",
    "situacao": "REGULAR" | "BLOQUEADO" | "ROUBO/FURTO",
    "licenciamento": {
        "exercicio": 2025,
        "vencimento": "2025-09-30",
        "pago": True,
        "valor": "...",
    },
    "ipva": {"exercicio": 2025, "vencimento": ..., "pago": True, "valor": ...},
    "multas": [{"auto": "...", "data": "...", "valor": "...", "descricao": "..."}],
    "restricoes": ["..."],
    "raw": {...},  # resposta original para auditoria
    "source": "infosimples" | "infosimples_mock",
  }
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

INFOSIMPLES_BASE_URL = "https://api.infosimples.com"

UFS_SUPORTADAS = frozenset({"SP", "MG", "GO"})

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize_placa(placa: str) -> str:
    """Remove separadores e normaliza placa para 7 alfanumericos.

    Aceita "ABC-1234", "ABC1A23", "abc 1234" e devolve "ABC1234"/"ABC1A23".
    """
    return _NON_ALNUM.sub("", (placa or "").upper())


class InfosimplesError(RuntimeError):
    """Transporte ou status nao-2xx do Infosimples."""


class InfosimplesUFNaoSuportadaError(ValueError):
    """UF fora das 3 cobertas no PR atual (SP/MG/GO).

    A API tem cobertura nacional, mas mantemos o whitelist explicito
    para evitar consultar UF nova sem teste/fixture associada.
    """


class InfosimplesClient(IntegrationClient):
    name = "infosimples"

    def __init__(
        self,
        *,
        api_token: str | None = None,
        base_url: str = INFOSIMPLES_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,  # consultas Detran sao lentas (~20-40s)
    ) -> None:
        self._api_token = api_token or ""
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout
        )

    @property
    def is_mock(self) -> bool:
        return not self._api_token

    async def health_check(self) -> bool:
        # Sem token -> mock, sempre saudavel.
        if self.is_mock:
            return True
        # Endpoint de status oficial. Falhar aqui nao significa que o
        # provider esta down -- pode ser nosso saldo zerado ou IP
        # nao-whitelistado. Dashboard agregado mostra como "warning".
        try:
            r = await self._client.get(
                "/api/v2/status",
                params={"token": self._api_token},
            )
        except httpx.HTTPError:
            return False
        return r.status_code == 200

    async def consultar_veiculo(
        self, placa: str, uf: str
    ) -> dict[str, Any]:
        uf_norm = uf.upper().strip()
        if uf_norm not in UFS_SUPORTADAS:
            raise InfosimplesUFNaoSuportadaError(
                f"UF {uf_norm!r} nao suportada. Disponiveis: "
                + ", ".join(sorted(UFS_SUPORTADAS))
            )
        placa_norm = re.sub(r"[^A-Z0-9]", "", placa.upper())
        if len(placa_norm) != 7:
            raise ValueError(
                f"placa invalida: {placa!r} (esperado 7 alfanumericos)"
            )

        if self.is_mock:
            return self._mock_response(placa_norm, uf_norm)

        endpoint = f"/api/v2/consultas/detran/{uf_norm.lower()}/veiculo"
        try:
            r = await self._client.post(
                endpoint,
                json={"token": self._api_token, "placa": placa_norm},
            )
        except httpx.HTTPError as exc:
            raise InfosimplesError(
                f"transporte Infosimples ({uf_norm}): {exc}"
            ) from exc
        if r.status_code != 200:
            raise InfosimplesError(
                f"Infosimples {uf_norm} status {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise InfosimplesError(
                f"resposta nao-JSON ({uf_norm}): {exc}"
            ) from exc

        # Infosimples envelopa em `code` (200=ok), `data` (lista de
        # consultas). Cada consulta vira um item com fields populados.
        if not isinstance(data, dict):
            raise InfosimplesError(
                f"resposta com formato inesperado: {type(data).__name__}"
            )
        code = data.get("code")
        if code != 200:
            raise InfosimplesError(
                f"Infosimples {uf_norm} code={code}: "
                f"{data.get('code_message') or data.get('message') or '?'}"
            )
        items = data.get("data") or []
        if not items:
            raise InfosimplesError(
                f"Infosimples {uf_norm} sem dados para placa {placa_norm}"
            )
        return self._normalize(items[0], placa_norm, uf_norm)

    def _normalize(
        self, raw: dict[str, Any], placa: str, uf: str
    ) -> dict[str, Any]:
        # A resposta real tem dezenas de campos com naming inconsistente
        # entre UFs (ex: SP usa "veiculo_marca_modelo", MG usa "marca").
        # Normalizamos para um schema unico que a UI/service consome
        # sem precisar saber de qual estado veio. Mantemos `raw` para
        # auditoria/debug.
        licenciamento = raw.get("licenciamento") or {}
        ipva = raw.get("ipva") or {}
        return {
            "uf": uf,
            "placa": placa,
            "renavam": raw.get("renavam") or raw.get("veiculo_renavam"),
            "chassi": raw.get("chassi") or raw.get("veiculo_chassi"),
            "marca_modelo": (
                raw.get("marca_modelo")
                or raw.get("veiculo_marca_modelo")
                or _join_or_none(raw.get("marca"), raw.get("modelo"))
            ),
            "ano_modelo": raw.get("ano_modelo")
            or raw.get("veiculo_ano_modelo"),
            "cor": raw.get("cor") or raw.get("veiculo_cor"),
            "combustivel": raw.get("combustivel")
            or raw.get("veiculo_combustivel"),
            "situacao": raw.get("situacao") or raw.get("situacao_veiculo"),
            "licenciamento": {
                "exercicio": licenciamento.get("exercicio"),
                "vencimento": licenciamento.get("vencimento"),
                "pago": licenciamento.get("pago"),
                "valor": licenciamento.get("valor"),
            }
            if licenciamento
            else None,
            "ipva": {
                "exercicio": ipva.get("exercicio"),
                "vencimento": ipva.get("vencimento"),
                "pago": ipva.get("pago"),
                "valor": ipva.get("valor"),
            }
            if ipva
            else None,
            "multas": list(raw.get("multas") or []),
            "restricoes": list(raw.get("restricoes") or []),
            "raw": raw,
            "source": "infosimples",
        }

    def _mock_response(self, placa: str, uf: str) -> dict[str, Any]:
        # Hash deterministico permite testes reprodutiveis. A combinacao
        # placa+uf gera um seed estavel; mesma placa em UFs diferentes
        # devolve dados diferentes (simula divergencia entre Detrans).
        digest = hashlib.sha1(f"{placa}|{uf}".encode()).hexdigest()
        idx = int(digest[:2], 16)
        marcas = ["VW/CONSTELLATION", "MERCEDES-BENZ/ACTROS", "SCANIA/R450"]
        cores = ["BRANCA", "PRATA", "PRETA"]
        situacoes = ["REGULAR", "REGULAR", "REGULAR", "BLOQUEADO"]
        # 25% chance de ter multas (idx 192-255)
        n_multas = (idx >> 6) & 0x3
        multas = [
            {
                "auto": f"AIT-{uf}{digest[2:8].upper()}{i}",
                "data": "2025-08-15",
                "valor": "195.23",
                "descricao": "Excesso de velocidade ate 20%",
            }
            for i in range(n_multas)
        ]
        return {
            "uf": uf,
            "placa": placa,
            "renavam": digest[:11].upper().replace("A", "0").replace("B", "1"),
            "chassi": ("9BW" + digest[:14].upper())[:17],
            "marca_modelo": marcas[idx % len(marcas)],
            "ano_modelo": 2018 + (idx % 6),
            "cor": cores[idx % len(cores)],
            "combustivel": "DIESEL",
            "situacao": situacoes[idx % len(situacoes)],
            "licenciamento": {
                "exercicio": 2025,
                "vencimento": "2025-09-30",
                "pago": idx % 5 != 0,  # 80% pago
                "valor": "163.42",
            },
            "ipva": {
                "exercicio": 2025,
                "vencimento": "2025-04-30",
                "pago": idx % 4 != 0,  # 75% pago
                "valor": "1234.56",
            },
            "multas": multas,
            "restricoes": [] if idx % 7 != 0 else ["ALIENACAO FIDUCIARIA"],
            "raw": None,
            "source": "infosimples_mock",
        }

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()


def _join_or_none(a: Any, b: Any) -> str | None:
    if not a and not b:
        return None
    return f"{a or ''}/{b or ''}".strip("/")
