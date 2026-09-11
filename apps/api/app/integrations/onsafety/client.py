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
- CPF e armazenado FORMATADO ("529.982.247-25") e filtros comparam a
  string exata -- filtrar pelos 11 digitos devolve vazio. Pull
  normaliza na saida; filtros server-side usam `format_cpf`.
- Escrita bem-sucedida responde 200 com corpo VAZIO; o id do registro
  sai de um lookup por CPF na sequencia.
- 401 = auth (token invalido/ambiente errado); 403 = validacao de
  negocio (ex.: push sem projeto vinculado). Smoke em homolog 2026-07-13.
- DELETE e SOFT-delete (`excluidoEm` + `ativo=false`) e a listagem
  padrao deles INCLUI excluidos. Por isso os metodos `list_*` daqui
  defaultam `ativo=True` -- sem filtro o pull ingeriria registros
  deletados. `ativo=None` traz tudo (auditoria/debug).

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
from typing import Any

import httpx

from app.core.cpf import format_cpf, normalize_cpf
from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

ONSAFETY_BASE_URL = "https://api.dev.onsafety.com.br"
ONSAFETY_PROD_HOST = "api.onsafety.com.br"

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
    "establishment.id,establishment.nome,establishment.codigoExterno,"
    "trabalhador.id,trabalhador.nome,trabalhador.cpf"
)
# Datas e NR confirmadas no spec OpenAPI publico da OnSafety
# (`GET /v3/api-docs`, schema `TreinamentoRealizado`, lido em
# 08/09/2026). `dataVencimento`/`validadeDias` NAO sao opcionais para
# nos: sem validade o documento de NR entra perene no dossie e o
# diagnostico o trata como conforme (ver `pull_treinamentos`).
# `/v2/treinamentos_realizados` -- a fonte boa dos treinamentos.
# Confirmado contra a base REAL em 11/09/2026: em
# `/v2/treinamentos_realizados_trabalhadores` a relacao
# `treinamentoRealizado` volta VAZIA (zero chaves, mesmo pedindo o
# objeto inteiro), entao descricao/sigla/validade sao inalcancaveis por
# la. Aqui vem tudo, com os participantes aninhados.
FIELDS_TREINAMENTOS_REALIZADOS = (
    "id,sigla,descricao,situacao,dataFim,dataVencimento,validadeDias,"
    "treinamentoCodigo.grupo,"
    "establishment.id,establishment.nome,establishment.codigoExterno,"
    "trabalhadores.id,trabalhadores.aprovado,trabalhadores.renovado,"
    "trabalhadores.certificateId,"
    "trabalhadores.trabalhador.id,trabalhadores.trabalhador.nome,"
    "trabalhadores.trabalhador.cpf"
)
FIELDS_TREINAMENTOS = (
    # SO os campos da propria participacao: a relacao
    # `treinamentoRealizado` volta vazia por este endpoint (medido na
    # base real em 11/09/2026), entao pedi-la aqui so gera ruido.
    # Quem precisa de sigla/validade usa `list_treinamentos_realizados`.
    "id,aprovado,renovado,certificateId,ativo,"
    "trabalhador.id,trabalhador.nome,trabalhador.cpf"
)

def _mock_cpf(digest: str) -> str:
    """CPF deterministico com digitos verificadores VALIDOS.

    O matching da Squad 2 valida CPF antes de casar (is_valid_cpf em
    dp_sesmt/cpf.py); um CPF aleatorio reprovaria ~99% das vezes e o
    dataset mock inteiro seria descartado em silencio.
    """
    base = [int(d) for d in str(int(digest[:12], 16) % 10**9).zfill(9)]
    resto = sum(d * (10 - i) for i, d in enumerate(base)) % 11
    base.append(0 if resto < 2 else 11 - resto)
    resto = sum(d * (11 - i) for i, d in enumerate(base)) % 11
    base.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, base))


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


class OnsafetyProdWriteBlockedError(OnsafetyError):
    """Escrita contra PRODUCAO bloqueada por guard-rail.

    O unico token que a Primor tem hoje e o de producao (ADR-001);
    qualquer `create_or_update` contra `api.onsafety.com.br` criaria/
    alteraria trabalhadores REAIS. Escrita em prod exige opt-in duplo:
    `allow_prod_write=True` no construtor (vem de
    `ONSAFETY_ALLOW_PROD_WRITE=true` no env) -- leitura nao e afetada.
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
        allow_prod_write: bool = False,
    ) -> None:
        self._api_token = api_token or ""
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout
        )
        self._allow_prod_write = allow_prod_write

    @property
    def is_mock(self) -> bool:
        return not self._api_token

    @property
    def is_prod(self) -> bool:
        return self._client.base_url.host == ONSAFETY_PROD_HOST

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
        ativo: bool | None = True,
    ) -> dict[str, Any]:
        """Lista trabalhadores cadastrados na OnSafety.

        `ativo=True` por default: o DELETE deles e soft-delete e a
        listagem padrao INCLUI excluidos. `ativo=None` traz tudo.

        Item: {id, nome, cpf, matricula, data_admissao, codigo_externo, ativo}
        """
        if self.is_mock:
            return self._mock_page(
                "trabalhadores", page, size, nome=nome, ativo=ativo
            )
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

    async def find_trabalhador_by_cpf(self, cpf: str) -> dict[str, Any] | None:
        """Localiza um trabalhador pelo CPF (None se nao existir).

        A OnSafety armazena o CPF FORMATADO ("529.982.247-25") e o
        filtro compara a string exata -- confirmado no smoke em homolog
        (2026-07-13): filtrar pelos 11 digitos devolve vazio. Este
        metodo aceita qualquer formato e converte para o pontuado.
        """
        cpf_norm = normalize_cpf(cpf)
        if len(cpf_norm) != 11:
            raise ValueError(f"cpf invalido: {cpf!r} (esperado 11 digitos)")
        if self.is_mock:
            digest = hashlib.sha1(cpf_norm.encode()).hexdigest()
            return {
                "id": f"mock-{digest[:24]}",
                "nome": None,
                "cpf": cpf_norm,
                "matricula": None,
                "data_admissao": None,
                "codigo_externo": None,
                "ativo": True,
            }
        raw = await self._get_page(
            "/v2/trabalhadores",
            fields=FIELDS_TRABALHADORES,
            page=0,
            size=1,
            cpf=format_cpf(cpf_norm),
        )
        content = raw.get("content") or []
        if not content:
            return None
        return self._normalize_trabalhador(content[0])

    async def count_trabalhadores(self, *, ativo: bool | None = True) -> int:
        """Total de trabalhadores. Via `totalElements` (o `/contar` da
        OnSafety esta quebrado -- erro Querydsl no backend deles).
        Default conta so ativos (soft-delete, ver docstring do modulo)."""
        if self.is_mock:
            return self._mock_page("trabalhadores", 0, 1, ativo=ativo)[
                "total"
            ]
        params: dict[str, Any] = {}
        if ativo is not None:
            params["ativo"] = ativo
        raw = await self._get_page(
            "/v2/trabalhadores", fields="id", page=0, size=1, **params
        )
        return int(raw.get("totalElements") or 0)

    async def list_exames_ocupacionais(
        self,
        *,
        page: int = 0,
        size: int = 100,
        ativo: bool | None = True,
    ) -> dict[str, Any]:
        """Lista exames ocupacionais (ASOs). Dado de saude -- todo pull
        deve gerar log de auditoria no caller (padrao dp_dossie_consultas).

        `ativo=True` por default (soft-delete, ver docstring do modulo).

        Item: {id, tipo_exame, data_aso, data_vencimento_aso,
               resultado_aso, situacao, ativo, trabalhador: {id, nome, cpf}}
        """
        if self.is_mock:
            return self._mock_page("exames", page, size, ativo=ativo)
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
        ativo: bool | None = True,
    ) -> dict[str, Any]:
        """Lista entregas de EPI (ficha de EPI).

        `ativo=True` por default (soft-delete, ver docstring do modulo).

        Item: {id, data_entrega, nome_equipamento, ca, quantidade,
               validade, previsao_devolucao, data_devolucao,
               status_entrega, ativo, trabalhador: {id, nome, cpf}}
        """
        if self.is_mock:
            return self._mock_page("controles_epi", page, size, ativo=ativo)
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
    ) -> dict[str, Any]:
        """Participacoes de trabalhadores em treinamentos.

        **Nao aceita filtro `ativo`**: a entidade nao tem essa coluna e
        a API responde 409 com erro do Hibernate
        (`could not resolve property: ativo`). Descoberto contra a base
        real em 11/09/2026 -- o default `ativo=True` que existia aqui
        quebrava toda chamada.

        Use `list_treinamentos_realizados` para o pull: a relacao
        `treinamentoRealizado` volta vazia por este caminho, entao
        descricao/sigla/validade nao chegam.

        Item: {id, aprovado, renovado, certificado_id,
               trabalhador: {id, nome, cpf}}
        """
        if self.is_mock:
            return self._mock_page("treinamentos", page, size)
        raw = await self._get_page(
            "/v2/treinamentos_realizados_trabalhadores",
            fields=FIELDS_TREINAMENTOS,
            page=page,
            size=size,
        )
        return self._envelope(raw, page, size, self._normalize_treinamento)

    async def list_treinamentos_realizados(
        self,
        *,
        page: int = 0,
        size: int = 100,
    ) -> dict[str, Any]:
        """Treinamentos realizados, COM os participantes aninhados.

        Fonte do pull de NRs: traz `sigla`, `descricao`, `validadeDias`
        e `dataVencimento` -- tudo que o outro endpoint nao entrega.

        Item: {id, sigla, descricao, grupo, situacao, data_fim,
               data_vencimento, validade_dias, projeto,
               participantes: [{id, aprovado, renovado, certificado_id,
                                trabalhador: {id, nome, cpf}}]}
        """
        if self.is_mock:
            return self._mock_page("treinamentos_realizados", page, size)
        raw = await self._get_page(
            "/v2/treinamentos_realizados",
            fields=FIELDS_TREINAMENTOS_REALIZADOS,
            page=page,
            size=size,
        )
        return self._envelope(
            raw, page, size, self._normalize_treinamento_realizado
        )

    # ------------------------------ push ---------------------------------

    async def create_or_update_trabalhador(
        self,
        *,
        nome: str,
        cpf: str,
        codigo_externo: str,
        projeto_id: str | None = None,
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

        `projeto_id` vincula o trabalhador a um estabelecimento/projeto
        OnSafety (entidade `Projeto`; a UI deles chama "estabelecimento").
        Sem ele a OnSafety recusa o push com 403 "Estabelecimento não
        especificado" (confirmado no smoke em homolog, 2026-07-13) --
        vem de ONSAFETY_PROJETO_ID ate existir mapeamento obra->projeto.

        Semantica confirmada em homolog (2026-07-13):
        - `isEditing=false` e upsert COMPLETO por CPF: cria se nao
          existe e atualiza campos de registro existente (mesmo id,
          `versao` incrementada). E o fluxo padrao -- N pushes = 1 registro.
        - `isEditing=true` exige `id` + `versao` (lock otimista) no body
          e responde 409 sem eles. Nao e usado no fluxo padrao; o
          parametro `is_editing` existe para um futuro fluxo de edicao
          concorrencia-segura.
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

        if self.is_prod and not self._allow_prod_write:
            raise OnsafetyProdWriteBlockedError(
                "escrita contra PRODUCAO OnSafety bloqueada "
                f"(base_url={self._client.base_url}). Use o ambiente de "
                "homologacao, ou -- se a escrita em prod for intencional "
                "-- setar ONSAFETY_ALLOW_PROD_WRITE=true."
            )

        body: dict[str, Any] = {
            "nome": nome,
            "cpf": cpf_norm,
            "codigoExterno": codigo_externo,
            "ativo": True,
            "validateCpf": True,
        }
        if projeto_id:
            body["projeto"] = {"id": projeto_id}
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
        # Sucesso vem com HTTP 200 e corpo VAZIO (confirmado no smoke em
        # homolog, 2026-07-13) -- o id sai de um lookup por CPF na
        # sequencia. Se a API algum dia devolver o objeto, aproveitamos.
        external_id: str | None = None
        if r.content:
            data = self._json_or_raise(r, "create_or_update")
            if isinstance(data, dict):
                external_id = data.get("id")
        if external_id is None:
            found = await self.find_trabalhador_by_cpf(cpf_norm)
            external_id = found["id"] if found else None
        return {
            "id": external_id,
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
        if r.status_code == 401:
            # A OnSafety devolve 401 tambem para token do OUTRO ambiente
            # (prod x dev) -- a mensagem ajuda a diagnosticar isso.
            raise OnsafetyAuthError(
                f"OnSafety {endpoint} status 401: token invalido ou de "
                f"outro ambiente (dev x prod). "
                f"Base URL atual: {self._client.base_url}"
            )
        if r.status_code >= 300:
            # 403 NAO e auth aqui: a OnSafety usa 403 para validacao de
            # negocio (ex.: "Estabelecimento não especificado" num push
            # sem projeto vinculado -- visto no smoke de 2026-07-13).
            # Auth de fato e sempre 401.
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
            "projeto": self._normalize_sub_projeto(raw.get("establishment")),
            "trabalhador": self._normalize_sub_trabalhador(
                raw.get("trabalhador")
            ),
        }

    def _normalize_treinamento(self, raw: dict[str, Any]) -> dict[str, Any]:
        realizado = raw.get("treinamentoRealizado") or {}
        codigo = realizado.get("treinamentoCodigo") or {}
        return {
            "id": raw.get("id"),
            "descricao": realizado.get("descricao"),
            "sigla": realizado.get("sigla"),
            # `grupo` do treinamentoCodigo e o rotulo normalizado da NR
            # na OnSafety -- preferimos ele a parsear `descricao`.
            "grupo": codigo.get("grupo"),
            "aprovado": raw.get("aprovado"),
            "renovado": raw.get("renovado"),
            "certificado_id": raw.get("certificateId"),
            "data_fim": _date10(realizado.get("dataFim")),
            "data_vencimento": _date10(realizado.get("dataVencimento")),
            "validade_dias": realizado.get("validadeDias"),
            "situacao": realizado.get("situacao"),
            "projeto": self._normalize_sub_projeto(
                realizado.get("establishment")
            ),
            "ativo": raw.get("ativo"),
            "trabalhador": self._normalize_sub_trabalhador(
                raw.get("trabalhador")
            ),
        }

    def _normalize_treinamento_realizado(
        self, raw: dict[str, Any]
    ) -> dict[str, Any]:
        codigo = raw.get("treinamentoCodigo") or {}
        participantes = [
            {
                "id": p.get("id"),
                "aprovado": p.get("aprovado"),
                "renovado": p.get("renovado"),
                "certificado_id": p.get("certificateId"),
                "trabalhador": self._normalize_sub_trabalhador(
                    p.get("trabalhador")
                ),
            }
            for p in (raw.get("trabalhadores") or [])
        ]
        return {
            "id": raw.get("id"),
            "sigla": raw.get("sigla"),
            "descricao": raw.get("descricao"),
            # Na base real o `grupo` e uma CATEGORIA descritiva
            # ("TREINAMENTOS, CAPACITACOES E EXERCICIOS SIMULADOS..."),
            # nao o rotulo da NR -- quem identifica a norma e a `sigla`
            # ("NR 6"). Mantido no payload para auditoria.
            "grupo": codigo.get("grupo"),
            "situacao": raw.get("situacao"),
            "data_fim": _date10(raw.get("dataFim")),
            "data_vencimento": _date10(raw.get("dataVencimento")),
            "validade_dias": raw.get("validadeDias"),
            "projeto": self._normalize_sub_projeto(raw.get("establishment")),
            "participantes": participantes,
        }

    def _normalize_sub_projeto(
        self, raw: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Projeto/estabelecimento da OnSafety (= obra, no nosso dominio).

        `codigoExterno` e o canal de reconciliacao com `obras_obra.codigo`
        (mesmo papel do `codigoExterno` do trabalhador).
        """
        if not raw:
            return None
        return {
            "id": raw.get("id"),
            "nome": raw.get("nome"),
            "codigo_externo": raw.get("codigoExterno"),
        }

    # ------------------------------ mock ---------------------------------

    # Totais fixos por recurso: pequenos o bastante para testes de
    # paginacao exercitarem "ultima pagina parcial" com size default.
    _MOCK_TOTAIS = {
        "trabalhadores": 12,
        "exames": 8,
        "controles_epi": 15,
        "treinamentos": 6,
        "treinamentos_realizados": 4,
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
        ativo: bool | None = None,
    ) -> dict[str, Any]:
        # Dataset deterministico: o item de indice global `i` e sempre
        # identico entre chamadas/paginas -- mesma semantica do mock do
        # Infosimples (testes reprodutiveis sem rede). Filtros sao
        # aplicados ANTES da paginacao e `total` reflete o filtrado --
        # mesma semantica do Spring no lado real.
        items = [
            self._mock_item(recurso, i)
            for i in range(self._mock_total(recurso))
        ]
        if nome:
            items = [
                it
                for it in items
                if nome.upper() in (it.get("nome") or "").upper()
            ]
        if ativo is not None:
            items = [it for it in items if it.get("ativo") is ativo]
        total = len(items)
        start = page * size
        return {
            "items": items[start : start + size],
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
        cpf = _mock_cpf(digest)
        if recurso == "trabalhadores":
            trabalhador = {"id": uid, "nome": nome, "cpf": cpf}
        else:
            # Os datasets-filhos (exames/EPI/treinamentos) referenciam
            # trabalhadores REAIS do dataset `trabalhadores` -- antes
            # cada recurso gerava CPF proprio e nada cruzava, entao
            # nenhum teste de pull exercitava o match de verdade
            # (follow-up da issue #39).
            base = self._mock_item(
                "trabalhadores", i % self._MOCK_TOTAIS["trabalhadores"]
            )
            trabalhador = {
                "id": base["id"],
                "nome": base["nome"],
                "cpf": base["cpf"],
            }
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
                "projeto": {
                    "id": uid,
                    "nome": f"OBRA {240 + (i % 3)} - TESTE",
                    "codigo_externo": str(240 + (i % 3)),
                },
                "trabalhador": trabalhador,
            }
        if recurso == "treinamentos":
            nrs = [
                ("NR-35", "NR-35 Trabalho em Altura"),
                ("NR-18", "NR-18 Construcao"),
                ("NR-06", "NR-06 EPI"),
            ]
            sigla, descricao = nrs[i % len(nrs)]
            ano_fim = 2025
            mes = 1 + (idx % 12)
            return {
                "id": uid,
                "descricao": descricao,
                "sigla": sigla,
                "grupo": sigla,
                # `i` (nao o digest) para o dataset ter sempre pelo
                # menos um reprovado -- o pull tem caminho proprio para
                # ele e precisa de cobertura estavel.
                "aprovado": i % 6 != 0,
                "renovado": idx % 3 == 0,
                "certificado_id": uid,
                "data_fim": f"{ano_fim}-{mes:02d}-20",
                "data_vencimento": f"{ano_fim + 2}-{mes:02d}-20",
                "validade_dias": 730,
                "situacao": "CONCLUIDO",
                "projeto": {
                    "id": uid,
                    "nome": f"OBRA {240 + (i % 3)} - TESTE",
                    "codigo_externo": str(240 + (i % 3)),
                },
                "ativo": True,
                "trabalhador": trabalhador,
            }
        if recurso == "treinamentos_realizados":
            # Shape espelha a base real: sigla e quem identifica a NR,
            # `grupo` e categoria descritiva, participantes aninhados.
            nrs = [
                ("NR 35", "Trabalho em Altura"),
                ("NR 18", "Construcao Civil"),
                ("NR 6", "Protecao Auditiva"),
                ("NR 12", "Maquinas e Equipamentos"),
            ]
            sigla, descricao = nrs[i % len(nrs)]
            mes = 1 + (idx % 12)
            participantes = [
                {
                    "id": f"{uid[:8]}-part-{j}",
                    "aprovado": not (i == 1 and j == 0),
                    "renovado": False,
                    "certificado_id": f"{uid[:8]}-cert-{j}",
                    "trabalhador": self._mock_item(
                        "trabalhadores",
                        (i * 2 + j) % self._MOCK_TOTAIS["trabalhadores"],
                    ),
                }
                for j in range(2)
            ]
            return {
                "id": uid,
                "sigla": sigla,
                "descricao": descricao,
                "grupo": "TREINAMENTOS, CAPACITACOES E EXERCICIOS SIMULADOS",
                "situacao": "CONCLUIDO",
                "data_fim": f"2025-{mes:02d}-20",
                # O ultimo e uma NR MAPEADA e sem validade -- exercita o
                # descarte que protege o diagnostico de ler NR vencida
                # como perene. (Se fosse NR nao mapeada, a checagem de
                # tipo barraria antes e esse caminho nunca rodaria.)
                "data_vencimento": (
                    None if i == len(nrs) - 1 else f"2027-{mes:02d}-20"
                ),
                "validade_dias": None if i == len(nrs) - 1 else 730,
                "projeto": {
                    "id": uid,
                    "nome": f"OBRA {240 + (i % 3)} - TESTE",
                    "codigo_externo": str(240 + (i % 3)),
                },
                "participantes": participantes,
            }
        raise ValueError(f"recurso mock desconhecido: {recurso!r}")

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()
