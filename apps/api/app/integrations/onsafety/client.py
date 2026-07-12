"""OnSafety -- SST (Saude e Seguranca do Trabalho) e EPIs (Modulo A).

A OnSafety (https://onsafety.com.br) e o sistema de SST da Primor:
ASOs/exames ocupacionais, fichas de EPI, treinamentos de NRs e
afastamentos. REST/JSON estilo Spring Data com auth por API key no
header `token`. Swagger: https://api.dev.onsafety.com.br/swagger-ui/.

Endpoints usados (validados contra o spec OpenAPI em 2026-07-12):

- `GET  /v2/trabalhadores`                          -- pull cadastro
- `GET  /v2/exames_ocupacionais`                    -- pull ASO (A.2)
- `GET  /v2/controles_epi`                          -- pull fichas de EPI
- `GET  /v2/treinamentos_realizados_trabalhadores`  -- pull treinamentos
- `POST /v2/trabalhadores/create_or_update`         -- push onboarding

Particularidades da API (confirmadas em chamadas reais):

- Listagens sao paginas Spring Data: `?page=&size=` na query, resposta
  `{content: [...], totalElements: N, ...}`.
- O parametro `fields` (projecao de colunas) e OBRIGATORIO nas
  listagens -- 409 sem ele. Usamos projecoes minimas por recurso
  (minimizacao LGPD: ASO e dado de saude).
- O endpoint `/v2/*/contar` esta quebrado no backend deles (erro
  Querydsl); contagens usam `totalElements` de uma pagina `size=1`.
- Homologacao: `api.dev.onsafety.com.br`; producao: `api.onsafety.com.br`
  (tokens NAO sao intercambiaveis entre ambientes). O default aqui e
  homologacao -- producao exige `ONSAFETY_BASE_URL` explicito.

Padrao do projeto: sem token (`ONSAFETY_TOKEN` vazio), o adapter cai
num mock deterministico para nao bloquear dev/CI -- mesmo padrao de
DirectData, Infosimples, Dominio e OneDrive.

Listagens normalizadas (mesmo envelope para os 4 recursos):
  {
    "items": [...],       # shape por recurso, ver _normalize_*
    "total": 5306,        # totalElements
    "page": 0,
    "size": 50,
    "source": "onsafety" | "onsafety_mock",
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

ONSAFETY_BASE_URL = "https://api.dev.onsafety.com.br"

# Projecoes `fields` por recurso. Minimas por LGPD; campos aninhados
# (`trabalhador.cpf`) seguem a sintaxe de projection do Spring, a
# validar em homologacao quando o token dev chegar -- se a API nao
# aceitar nested fields, ajustar apenas estas constantes.
FIELDS_TRABALHADORES = "id,nome,cpf,matricula,dataAdmissao,codigoExterno,ativo"
FIELDS_EXAMES = (
    "id,tipoExameString,dataAso,dataVencimentoAso,resultadoAso,situacao,"
    "ativo,trabalhador.id,trabalhador.nome,trabalhador.cpf"
)
FIELDS_CONTROLES_EPI = (
    "id,dataEntrega,nomeEquipamento,ca,quantidade,validade,"
    "previsaoDevolucao,dataDevolucao,statusEntrega,ativo,"
    "trabalhador.id,trabalhador.nome,trabalhador.cpf"
)
FIELDS_TREINAMENTOS = (
    "id,aprovado,renovado,certificateId,ativo,"
    "trabalhador.id,trabalhador.nome,trabalhador.cpf,"
    "treinamentoRealizado.id,treinamentoRealizado.descricao"
)

_NON_DIGIT = re.compile(r"\D")


def normalize_cpf(cpf: str) -> str:
    """Remove pontuacao e devolve so os 11 digitos ("529.982.247-25" -> "52998224725")."""
    return _NON_DIGIT.sub("", cpf or "")


def _date10(value: Any) -> str | None:
    """Trunca date-time ISO da OnSafety ("2025-01-02T00:00:00") para "2025-01-02"."""
    if not value or not isinstance(value, str):
        return None
    return value[:10]


class OnsafetyError(RuntimeError):
    """Transporte, status nao-2xx ou payload inesperado da OnSafety."""


class OnsafetyAuthError(OnsafetyError):
    """401/403 -- token invalido ou de outro ambiente (dev x prod).

    Separado de `OnsafetyError` porque auth nao e transitorio: nao
    retentamos, precisa rotar o token (ou corrigir a base URL -- token
    de producao contra `api.dev.` devolve 401).
    """


class OnsafetyClient(IntegrationClient):
    name = "onsafety"

    def __init__(
        self,
        *,
        api_token: str | None = None,
        base_url: str = ONSAFETY_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
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
        if self.is_mock:
            return True
        try:
            page = await self._get_page(
                "/v2/trabalhadores", fields="id", page=0, size=1
            )
        except OnsafetyError:
            return False
        return isinstance(page.get("totalElements"), int)

    # ------------------------------ pull ---------------------------------

    async def list_trabalhadores(
        self,
        *,
        page: int = 0,
        size: int = 100,
        nome: str | None = None,
        ativo: bool | None = None,
    ) -> dict[str, Any]:
        """Lista trabalhadores cadastrados na OnSafety.

        Item: {id, nome, cpf, matricula, data_admissao, codigo_externo, ativo}
        """
        if self.is_mock:
            return self._mock_page("trabalhadores", page, size, nome=nome)
        params: dict[str, Any] = {}
        if nome:
            params["nome"] = nome
        if ativo is not None:
            params["ativo"] = ativo
        raw = await self._get_page(
            "/v2/trabalhadores",
            fields=FIELDS_TRABALHADORES,
            page=page,
            size=size,
            **params,
        )
        return self._envelope(raw, page, size, self._normalize_trabalhador)

    async def count_trabalhadores(self) -> int:
        """Total de trabalhadores. Via `totalElements` (o `/contar` da
        OnSafety esta quebrado -- erro Querydsl no backend deles)."""
        if self.is_mock:
            return self._mock_total("trabalhadores")
        raw = await self._get_page(
            "/v2/trabalhadores", fields="id", page=0, size=1
        )
        return int(raw.get("totalElements") or 0)

    async def list_exames_ocupacionais(
        self,
        *,
        page: int = 0,
        size: int = 100,
        ativo: bool | None = None,
    ) -> dict[str, Any]:
        """Lista exames ocupacionais (ASOs). Dado de saude -- todo pull
        deve gerar log de auditoria no caller (padrao dp_dossie_consultas).

        Item: {id, tipo_exame, data_aso, data_vencimento_aso,
               resultado_aso, situacao, ativo, trabalhador: {id, nome, cpf}}
        """
        if self.is_mock:
            return self._mock_page("exames", page, size)
        params: dict[str, Any] = {}
        if ativo is not None:
            params["ativo"] = ativo
        raw = await self._get_page(
            "/v2/exames_ocupacionais",
            fields=FIELDS_EXAMES,
            page=page,
            size=size,
            **params,
        )
        return self._envelope(raw, page, size, self._normalize_exame)

    async def list_controles_epi(
        self,
        *,
        page: int = 0,
        size: int = 100,
        data_de: str | None = None,
        data_ate: str | None = None,
        ativo: bool | None = None,
    ) -> dict[str, Any]:
        """Lista entregas de EPI (ficha de EPI).

        Item: {id, data_entrega, nome_equipamento, ca, quantidade,
               validade, previsao_devolucao, data_devolucao,
               status_entrega, ativo, trabalhador: {id, nome, cpf}}
        """
        if self.is_mock:
            return self._mock_page("controles_epi", page, size)
        params: dict[str, Any] = {}
        if data_de:
            params["dataDe"] = data_de
        if data_ate:
            params["dataAte"] = data_ate
        if ativo is not None:
            params["ativo"] = ativo
        raw = await self._get_page(
            "/v2/controles_epi",
            fields=FIELDS_CONTROLES_EPI,
            page=page,
            size=size,
            **params,
        )
        return self._envelope(raw, page, size, self._normalize_controle_epi)

    async def list_treinamentos_trabalhadores(
        self,
        *,
        page: int = 0,
        size: int = 100,
        ativo: bool | None = None,
    ) -> dict[str, Any]:
        """Lista participacoes de trabalhadores em treinamentos (NRs).

        Item: {id, descricao, aprovado, renovado, certificado_id, ativo,
               trabalhador: {id, nome, cpf}}
        """
        if self.is_mock:
            return self._mock_page("treinamentos", page, size)
        params: dict[str, Any] = {}
        if ativo is not None:
            params["ativo"] = ativo
        raw = await self._get_page(
            "/v2/treinamentos_realizados_trabalhadores",
            fields=FIELDS_TREINAMENTOS,
            page=page,
            size=size,
            **params,
        )
        return self._envelope(raw, page, size, self._normalize_treinamento)

    # ------------------------------ push ---------------------------------

    async def create_or_update_trabalhador(
        self,
        *,
        nome: str,
        cpf: str,
        codigo_externo: str,
        matricula: str | None = None,
        email: str | None = None,
        data_admissao: str | None = None,
        data_nascimento: str | None = None,
        is_editing: bool = False,
    ) -> dict[str, Any]:
        """Cria ou atualiza um trabalhador na OnSafety (onboarding A).

        `codigo_externo` carrega o nosso employee id -- e a chave de
        reconciliacao entre Motor Central e OnSafety (idempotencia do
        `sync_onboarding`). Datas em ISO `YYYY-MM-DD`.
        """
        cpf_norm = normalize_cpf(cpf)
        if len(cpf_norm) != 11:
            raise ValueError(f"cpf invalido: {cpf!r} (esperado 11 digitos)")
        if not nome.strip():
            raise ValueError("nome vazio")
        if not codigo_externo.strip():
            raise ValueError("codigo_externo vazio")

        if self.is_mock:
            digest = hashlib.sha1(cpf_norm.encode()).hexdigest()
            return {
                "id": f"mock-{digest[:24]}",
                "nome": nome,
                "cpf": cpf_norm,
                "codigo_externo": codigo_externo,
                "source": "onsafety_mock",
            }

        body: dict[str, Any] = {
            "nome": nome,
            "cpf": cpf_norm,
            "codigoExterno": codigo_externo,
            "ativo": True,
            "validateCpf": True,
        }
        if matricula:
            body["matricula"] = matricula
        if email:
            body["email"] = email
        if data_admissao:
            body["dataAdmissao"] = f"{data_admissao}T00:00:00"
        if data_nascimento:
            body["dataNascimento"] = f"{data_nascimento}T00:00:00"

        try:
            r = await self._client.post(
                "/v2/trabalhadores/create_or_update",
                params={"isEditing": is_editing},
                json=body,
                headers={"token": self._api_token},
            )
        except httpx.HTTPError as exc:
            raise OnsafetyError(
                f"transporte OnSafety (create_or_update): {exc}"
            ) from exc
        self._raise_for_status(r, "create_or_update")
        data = self._json_or_raise(r, "create_or_update")
        return {
            "id": data.get("id") if isinstance(data, dict) else None,
            "nome": nome,
            "cpf": cpf_norm,
            "codigo_externo": codigo_externo,
            "source": "onsafety",
        }

    # --------------------------- HTTP interno ----------------------------

    async def _get_page(
        self,
        endpoint: str,
        *,
        fields: str,
        page: int,
        size: int,
        **params: Any,
    ) -> dict[str, Any]:
        query = {"fields": fields, "page": page, "size": size, **params}
        try:
            r = await self._client.get(
                endpoint, params=query, headers={"token": self._api_token}
            )
        except httpx.HTTPError as exc:
            raise OnsafetyError(
                f"transporte OnSafety ({endpoint}): {exc}"
            ) from exc
        self._raise_for_status(r, endpoint)
        data = self._json_or_raise(r, endpoint)
        if not isinstance(data, dict) or "content" not in data:
            raise OnsafetyError(
                f"OnSafety {endpoint}: pagina Spring esperada, veio "
                f"{type(data).__name__}"
            )
        return data

    def _raise_for_status(self, r: httpx.Response, endpoint: str) -> None:
        if r.status_code in (401, 403):
            # A OnSafety devolve 401 tambem para token do OUTRO ambiente
            # (prod x dev) -- a mensagem ajuda a diagnosticar isso.
            raise OnsafetyAuthError(
                f"OnSafety {endpoint} status {r.status_code}: token "
                f"invalido ou de outro ambiente (dev x prod). "
                f"Base URL atual: {self._client.base_url}"
            )
        if r.status_code >= 300:
            raise OnsafetyError(
                f"OnSafety {endpoint} status {r.status_code}: {r.text[:200]}"
            )

    def _json_or_raise(self, r: httpx.Response, endpoint: str) -> Any:
        try:
            return r.json()
        except ValueError as exc:
            raise OnsafetyError(
                f"resposta nao-JSON OnSafety ({endpoint}): {exc}"
            ) from exc

    # --------------------------- normalizacao ----------------------------

    def _envelope(
        self,
        raw: dict[str, Any],
        page: int,
        size: int,
        normalize: Any,
    ) -> dict[str, Any]:
        return {
            "items": [normalize(item) for item in raw.get("content") or []],
            "total": int(raw.get("totalElements") or 0),
            "page": page,
            "size": size,
            "source": "onsafety",
        }

    @staticmethod
    def _normalize_trabalhador(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": raw.get("id"),
            "nome": raw.get("nome"),
            "cpf": normalize_cpf(raw.get("cpf") or "") or None,
            "matricula": raw.get("matricula"),
            "data_admissao": _date10(raw.get("dataAdmissao")),
            "codigo_externo": raw.get("codigoExterno"),
            "ativo": raw.get("ativo"),
        }

    @staticmethod
    def _normalize_sub_trabalhador(
        raw: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not raw:
            return None
        return {
            "id": raw.get("id"),
            "nome": raw.get("nome"),
            "cpf": normalize_cpf(raw.get("cpf") or "") or None,
        }

    def _normalize_exame(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": raw.get("id"),
            "tipo_exame": raw.get("tipoExameString"),
            "data_aso": _date10(raw.get("dataAso")),
            "data_vencimento_aso": _date10(raw.get("dataVencimentoAso")),
            "resultado_aso": raw.get("resultadoAso"),
            "situacao": raw.get("situacao"),
            "ativo": raw.get("ativo"),
            "trabalhador": self._normalize_sub_trabalhador(
                raw.get("trabalhador")
            ),
        }

    def _normalize_controle_epi(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": raw.get("id"),
            "data_entrega": _date10(raw.get("dataEntrega")),
            "nome_equipamento": raw.get("nomeEquipamento"),
            "ca": raw.get("ca"),
            "quantidade": raw.get("quantidade"),
            "validade": raw.get("validade"),
            "previsao_devolucao": _date10(raw.get("previsaoDevolucao")),
            "data_devolucao": _date10(raw.get("dataDevolucao")),
            "status_entrega": raw.get("statusEntrega"),
            "ativo": raw.get("ativo"),
            "trabalhador": self._normalize_sub_trabalhador(
                raw.get("trabalhador")
            ),
        }

    def _normalize_treinamento(self, raw: dict[str, Any]) -> dict[str, Any]:
        realizado = raw.get("treinamentoRealizado") or {}
        return {
            "id": raw.get("id"),
            "descricao": realizado.get("descricao"),
            "aprovado": raw.get("aprovado"),
            "renovado": raw.get("renovado"),
            "certificado_id": raw.get("certificateId"),
            "ativo": raw.get("ativo"),
            "trabalhador": self._normalize_sub_trabalhador(
                raw.get("trabalhador")
            ),
        }

    # ------------------------------ mock ---------------------------------

    # Totais fixos por recurso: pequenos o bastante para testes de
    # paginacao exercitarem "ultima pagina parcial" com size default.
    _MOCK_TOTAIS = {
        "trabalhadores": 12,
        "exames": 8,
        "controles_epi": 15,
        "treinamentos": 6,
    }
    _MOCK_NOMES = [
        "JOSE DA SILVA",
        "MARIA SOUZA",
        "JOAO PEREIRA",
        "ANA OLIVEIRA",
        "CARLOS SANTOS",
        "FERNANDA LIMA",
    ]

    def _mock_total(self, recurso: str) -> int:
        return self._MOCK_TOTAIS[recurso]

    def _mock_page(
        self,
        recurso: str,
        page: int,
        size: int,
        *,
        nome: str | None = None,
    ) -> dict[str, Any]:
        # Dataset deterministico: o item de indice global `i` e sempre
        # identico entre chamadas/paginas -- mesma semantica do mock do
        # Infosimples (testes reprodutiveis sem rede).
        total = self._mock_total(recurso)
        start = page * size
        items = [
            self._mock_item(recurso, i)
            for i in range(start, min(start + size, total))
        ]
        if nome:
            items = [
                it
                for it in items
                if nome.upper() in (it.get("nome") or "").upper()
            ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "size": size,
            "source": "onsafety_mock",
        }

    def _mock_item(self, recurso: str, i: int) -> dict[str, Any]:
        digest = hashlib.sha1(f"onsafety|{recurso}|{i}".encode()).hexdigest()
        idx = int(digest[:2], 16)
        uid = (
            f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-"
            f"{digest[16:20]}-{digest[20:32]}"
        )
        nome = self._MOCK_NOMES[i % len(self._MOCK_NOMES)]
        cpf = str(int(digest[:12], 16) % 10**11).zfill(11)
        trabalhador = {"id": uid, "nome": nome, "cpf": cpf}
        if recurso == "trabalhadores":
            return {
                "id": uid,
                "nome": nome,
                "cpf": cpf,
                "matricula": f"MAT{1000 + i}",
                "data_admissao": f"202{i % 5}-{1 + (idx % 12):02d}-15",
                "codigo_externo": None,
                "ativo": i % 5 != 4,  # ~80% ativos
            }
        if recurso == "exames":
            resultados = [1, 1, 1, 2]  # 1=apto, 2=inapto (minoria)
            return {
                "id": uid,
                "tipo_exame": ["Admissional", "Periódico", "Demissional"][
                    i % 3
                ],
                "data_aso": f"2025-{1 + (idx % 12):02d}-10",
                "data_vencimento_aso": f"2026-{1 + (idx % 12):02d}-10",
                "resultado_aso": resultados[idx % len(resultados)],
                "situacao": "VALIDO" if idx % 4 != 0 else "VENCIDO",
                "ativo": True,
                "trabalhador": trabalhador,
            }
        if recurso == "controles_epi":
            equipamentos = [
                "CAPACETE CLASSE B",
                "LUVA DE VAQUETA",
                "BOTINA DE SEGURANCA",
                "PROTETOR AURICULAR",
            ]
            return {
                "id": uid,
                "data_entrega": f"2025-{1 + (idx % 12):02d}-05",
                "nome_equipamento": equipamentos[i % len(equipamentos)],
                "ca": 10000 + idx * 37,
                "quantidade": 1 + (idx % 3),
                "validade": f"2026-{1 + (idx % 12):02d}-05",
                "previsao_devolucao": None,
                "data_devolucao": None,
                "status_entrega": "ENTREGUE",
                "ativo": True,
                "trabalhador": trabalhador,
            }
        if recurso == "treinamentos":
            nrs = ["NR-35 Trabalho em Altura", "NR-18 Construcao", "NR-06 EPI"]
            return {
                "id": uid,
                "descricao": nrs[i % len(nrs)],
                "aprovado": idx % 5 != 0,
                "renovado": idx % 3 == 0,
                "certificado_id": uid,
                "ativo": True,
                "trabalhador": trabalhador,
            }
        raise ValueError(f"recurso mock desconhecido: {recurso!r}")

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()
