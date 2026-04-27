"""Infosimples -- consultas Detran (multas, IPVA, licenciamento, debitos)
e CREA (ART, profissional, empresa).

A Infosimples (https://infosimples.com) agrega consultas em ~26 Detrans
estaduais e ~10 conselhos profissionais (CREA, CRM, OAB...). Expoe
REST/JSON com auth por token. Usamos:

- `/api/v2/consultas/detran/<uf>/veiculo` (B.3, PR #14) -- SP/MG/GO
- `/api/v2/consultas/crea/<uf>/{art,profissional,empresa}` (D.6 fase 2,
  PR #28) -- mesmas 3 UFs por compatibilidade

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

# CREA: tipos de consulta suportados na fase 2.
#   - "art"          -> valida numero de ART, devolve profissional/servico
#   - "profissional" -> valida registro do engenheiro (CPF ou n. CREA)
#   - "empresa"      -> lista ARTs registradas pelo CNPJ
# Tipos sao normalizados em lower-case (Infosimples e case-sensitive).
CREA_TIPOS_SUPORTADOS = frozenset({"art", "profissional", "empresa"})

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


class InfosimplesCreaTipoNaoSuportadoError(ValueError):
    """`tipo` fora de {art, profissional, empresa}."""


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

    # ----------------------------- CREA (D.6 fase 2) --------------------

    async def consultar_crea(
        self,
        uf: str,
        tipo: str,
        identificador: str,
    ) -> dict[str, Any]:
        """Consulta CREA via Infosimples para ART / profissional / empresa.

        Resposta normalizada (mesma forma para os 3 tipos -- fields nao
        aplicaveis vem como `None`):
          {
            "uf": "MG",
            "tipo": "art",
            "identificador": "MG2023ABC123",
            "art": {
                "numero": "MG2023ABC123",
                "tipo_servico": "OBRA / SERVICO TECNICO",
                "valor_contrato": "1500000.00",
                "data_registro": "2023-05-12",
                "data_inicio": "2023-06-01",
                "data_termino_previsto": "2024-12-31",
                "situacao": "ATIVA",  # ATIVA | BAIXADA | CANCELADA
            } | None,
            "profissional": {
                "registro_crea": "MG-145678/D",
                "nome": "JOSE DA SILVA",
                "cpf": "***",
                "titulo": "ENGENHEIRO CIVIL",
                "situacao": "REGULAR",  # REGULAR | SUSPENSO | CANCELADO
            } | None,
            "empresa": {
                "cnpj": "00.000.000/0001-00",
                "razao_social": "PRIMOR CONSTRUTORA LTDA",
                "registro_crea": "PJ-12345/MG",
                "situacao": "REGULAR",
                "arts_count": 23,  # so devolvido em tipo=empresa
            } | None,
            "raw": {...},
            "source": "infosimples" | "infosimples_mock",
          }

        Levanta:
            InfosimplesUFNaoSuportadaError -- UF fora de SP/MG/GO
            InfosimplesCreaTipoNaoSuportadoError -- tipo invalido
            ValueError -- identificador vazio
            InfosimplesError -- erro de transporte ou response code != 200
        """
        uf_norm = uf.upper().strip()
        if uf_norm not in UFS_SUPORTADAS:
            raise InfosimplesUFNaoSuportadaError(
                f"UF {uf_norm!r} nao suportada. Disponiveis: "
                + ", ".join(sorted(UFS_SUPORTADAS))
            )
        tipo_norm = tipo.lower().strip()
        if tipo_norm not in CREA_TIPOS_SUPORTADOS:
            raise InfosimplesCreaTipoNaoSuportadoError(
                f"tipo {tipo!r} nao suportado. Use: "
                + ", ".join(sorted(CREA_TIPOS_SUPORTADOS))
            )
        ident_norm = (identificador or "").strip()
        if not ident_norm:
            raise ValueError("identificador vazio")
        # Limites razoaveis para evitar abuso (numeros de ART tem ate
        # ~24 chars; CNPJ formatado 18; n. CREA ate ~16).
        if len(ident_norm) > 64:
            raise ValueError(
                f"identificador muito longo: {len(ident_norm)} chars (max 64)"
            )

        if self.is_mock:
            return self._mock_response_crea(ident_norm, uf_norm, tipo_norm)

        endpoint = f"/api/v2/consultas/crea/{uf_norm.lower()}/{tipo_norm}"
        try:
            r = await self._client.post(
                endpoint,
                json={
                    "token": self._api_token,
                    "identificador": ident_norm,
                },
            )
        except httpx.HTTPError as exc:
            raise InfosimplesError(
                f"transporte Infosimples CREA ({uf_norm}/{tipo_norm}): {exc}"
            ) from exc
        if r.status_code != 200:
            raise InfosimplesError(
                f"Infosimples CREA {uf_norm}/{tipo_norm} status "
                f"{r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise InfosimplesError(
                f"resposta nao-JSON CREA ({uf_norm}/{tipo_norm}): {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise InfosimplesError(
                f"resposta CREA com formato inesperado: {type(data).__name__}"
            )
        code = data.get("code")
        if code != 200:
            raise InfosimplesError(
                f"Infosimples CREA {uf_norm}/{tipo_norm} code={code}: "
                f"{data.get('code_message') or data.get('message') or '?'}"
            )
        items = data.get("data") or []
        if not items:
            raise InfosimplesError(
                f"Infosimples CREA {uf_norm}/{tipo_norm}: sem dados para "
                f"{ident_norm!r}"
            )
        return self._normalize_crea(items[0], ident_norm, uf_norm, tipo_norm)

    def _normalize_crea(
        self,
        raw: dict[str, Any],
        identificador: str,
        uf: str,
        tipo: str,
    ) -> dict[str, Any]:
        # Como Detran, response real tem naming inconsistente entre
        # consultas (CREA-MG x CREA-SP). Normalizamos para um schema
        # unico que a UI/service consome sem branching por UF.
        art_raw = raw.get("art") or (raw if tipo == "art" else None)
        prof_raw = raw.get("profissional") or (
            raw if tipo == "profissional" else None
        )
        emp_raw = raw.get("empresa") or (raw if tipo == "empresa" else None)
        return {
            "uf": uf,
            "tipo": tipo,
            "identificador": identificador,
            "art": _normalize_crea_art(art_raw) if art_raw else None,
            "profissional": _normalize_crea_profissional(prof_raw)
            if prof_raw
            else None,
            "empresa": _normalize_crea_empresa(emp_raw) if emp_raw else None,
            "raw": raw,
            "source": "infosimples",
        }

    def _mock_response_crea(
        self, identificador: str, uf: str, tipo: str
    ) -> dict[str, Any]:
        # Hash deterministico igual ao mock de Detran -- mesma combinacao
        # (identificador, uf, tipo) gera mesma resposta. Permite os
        # testes exercitarem o caminho real (`source=infosimples_mock`)
        # sem token e sem network.
        digest = hashlib.sha1(
            f"{identificador}|{uf}|{tipo}".encode()
        ).hexdigest()
        idx = int(digest[:2], 16)
        situacoes_art = ["ATIVA", "ATIVA", "ATIVA", "BAIXADA", "CANCELADA"]
        situacoes_prof = ["REGULAR", "REGULAR", "REGULAR", "SUSPENSO"]
        titulos = ["ENGENHEIRO CIVIL", "ENGENHEIRO ELETRICISTA", "ARQUITETO"]
        nomes = [
            "JOSE DA SILVA",
            "MARIA SOUZA",
            "JOAO PEREIRA",
            "ANA OLIVEIRA",
        ]

        art = (
            {
                "numero": identificador
                if tipo == "art"
                else f"{uf}{2020 + (idx % 6)}{digest[:6].upper()}",
                "tipo_servico": "OBRA / SERVICO TECNICO",
                "valor_contrato": f"{(idx + 1) * 50000:.2f}",
                "data_registro": f"{2020 + (idx % 6)}-{1 + (idx % 12):02d}-15",
                "data_inicio": f"{2020 + (idx % 6)}-{1 + (idx % 12):02d}-20",
                "data_termino_previsto": (
                    f"{2021 + (idx % 6)}-{1 + ((idx + 5) % 12):02d}-31"
                ),
                "situacao": situacoes_art[idx % len(situacoes_art)],
            }
            if tipo == "art"
            else None
        )
        profissional = (
            {
                "registro_crea": f"{uf}-{100000 + idx * 137}/D",
                "nome": nomes[idx % len(nomes)],
                "cpf": "***.***.***-**",  # mascarado por LGPD no mock
                "titulo": titulos[idx % len(titulos)],
                "situacao": situacoes_prof[idx % len(situacoes_prof)],
            }
            if tipo == "profissional"
            else None
        )
        empresa = (
            {
                "cnpj": identificador,
                "razao_social": "PRIMOR CONSTRUTORA LTDA"
                if (idx % 2) == 0
                else "ZAG ENGENHARIA LTDA",
                "registro_crea": f"PJ-{1000 + idx * 13}/{uf}",
                "situacao": situacoes_prof[idx % len(situacoes_prof)],
                "arts_count": (idx % 30) + 1,
            }
            if tipo == "empresa"
            else None
        )
        return {
            "uf": uf,
            "tipo": tipo,
            "identificador": identificador,
            "art": art,
            "profissional": profissional,
            "empresa": empresa,
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


def _normalize_crea_art(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "numero": raw.get("numero") or raw.get("art_numero"),
        "tipo_servico": (
            raw.get("tipo_servico")
            or raw.get("tipo_obra")
            or raw.get("descricao_servico")
        ),
        "valor_contrato": (
            raw.get("valor_contrato")
            or raw.get("valor")
            or raw.get("valor_total")
        ),
        "data_registro": raw.get("data_registro") or raw.get("registrada_em"),
        "data_inicio": raw.get("data_inicio") or raw.get("inicio"),
        "data_termino_previsto": (
            raw.get("data_termino_previsto")
            or raw.get("termino_previsto")
            or raw.get("data_fim")
        ),
        "situacao": raw.get("situacao") or raw.get("status"),
    }


def _normalize_crea_profissional(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "registro_crea": (
            raw.get("registro_crea")
            or raw.get("numero_registro")
            or raw.get("registro")
        ),
        "nome": raw.get("nome") or raw.get("nome_profissional"),
        "cpf": raw.get("cpf"),
        "titulo": raw.get("titulo") or raw.get("formacao"),
        "situacao": raw.get("situacao") or raw.get("status"),
    }


def _normalize_crea_empresa(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "cnpj": raw.get("cnpj"),
        "razao_social": raw.get("razao_social") or raw.get("nome"),
        "registro_crea": (
            raw.get("registro_crea")
            or raw.get("numero_registro")
            or raw.get("registro")
        ),
        "situacao": raw.get("situacao") or raw.get("status"),
        "arts_count": raw.get("arts_count") or raw.get("total_arts"),
    }
