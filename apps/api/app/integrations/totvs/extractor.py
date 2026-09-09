"""Extracao de lancamentos financeiros do TOTVS RM, atras de uma interface.

Por que interface: a forma de extrair ainda esta em aberto no ticket
30268517 -- REST (WebAPI, `ApiPort`) ou wsConsultaSQL (SOAP, `HttpPort`).
Leitura direta no banco esta FORA: o ambiente da Primor e TOTVS Cloud
(TCloud), confirmado em 24/08/2026. Trocar de implementacao nao pode
custar reescrita do modulo, dai `TotvsExtractor`.

Instancia da Primor: 12.1.2510.136 (producao) / 12.1.2510.170 (dev).
Essa versao esta acima de todos os cortes que importam:

  >= 12.1.15    controle de licenca + Basic default
  >= 12.1.25    `HttpPort` (SOAP) e `ApiPort` (REST) separaveis
  >= 12.1.2306  consumo aparece como `ApiPool` no monitor + log LS006
  >= 12.1.2310  `JWTTokenExpireMinutes` configuravel
  patches 12.1.2302 p121 / 2209 p195 / 2205 p246
                APIs de Coligada/Usuarios/Perfil/Parametros SEM consumo
                de licenca -- e por isso que o health_check usa
                `/api/framework/v1/coligadas`.

CUSTO DE LICENCA (o que dita o desenho): licenca de WebService e
consumida POR REQUISICAO DE DADOS e so liberada quando a requisicao
termina -- 3 chamadas simultaneas = 3 licencas. A geracao de token em
`/api/connect/token` NAO consome licenca (confirmado pela TOTVS em
27/08/2026), entao renovar e de graca e o default de 5 minutos nao e
problema. Ha reaproveitamento da mesma
licenca por 30s entre requisicoes NAO concorrentes. Por isso o pull e
estritamente sequencial, encadeado, e roda so no worker sob lock
single-flight (nunca na API). A escala de consumo sobe ate a TOTVS
Full e nao e nomeavel: sem cuidado, o pull noturno come um assento
TOTVS Full da operacao.

RESPONDIDO pela TOTVS (Eduarda Soares, 31/08/2026), e isso FECHOU a
escolha do caminho de extracao:

  - NAO existe API REST de lancamento financeiro nativa que atenda a
    extracao massiva com paginacao. As APIs do modulo Gestao Financeira
    nao cobrem esse caso. **O caminho e o wsConsultaSQL.**
  - Chave unica do lancamento na FLAN: CODCOLIGADA + IDLAN.
  - Contraparte: relacionar FLAN com FCFO por CODCFO **e** CODCOLIGADA;
    o CNPJ/CPF sai em FCFO.CGCCFO.
  - Campos: VALORORIGINAL, DATAVENCIMENTO, DATAEMISSAO, STATUSLAN.

O `RestExtractor` continua no codigo porque a interface prove que a
troca e barata e porque as APIs de framework (coligadas) seguem uteis
-- mas NAO e o caminho para lancamentos.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime
from typing import Any, Protocol
from xml.etree import ElementTree as ET

import httpx

from app.core.valores import coerce_valor_rm

logger = logging.getLogger(__name__)

# Caminhos. O de ConsultaSQL e estavel (documentado pela TOTVS); o de
# REST e PLACEHOLDER ate o time RM Gestao Financeira responder qual e
# o endpoint de lancamentos -- por isso e parametro, nao constante fixa.
CONSULTASQL_PATH = "/wsConsultaSQL/MEX"
LANCAMENTOS_PATH_PADRAO = "/api/fin/v1/lancamentos"  # A CONFIRMAR
COLIGADAS_PATH = "/api/framework/v1/coligadas"  # sem consumo de licenca
TOKEN_PATH = "/api/connect/token"

# Mapeamento de campos da FLAN. Os quatro campos de negocio foram
# CONFIRMADOS pela TOTVS em 31/08/2026 (Eduarda Soares). `NOMECFO` e um
# ALIAS que a sentenca SQL define sobre a coluna de nome da FCFO -- ver
# a sentenca em docs/integrations.md. Trocar qualquer nome aqui nao
# deve exigir mudanca em nenhum outro arquivo.
CAMPO_COLIGADA = "CODCOLIGADA"
CAMPO_FILIAL = "CODFILIAL"
CAMPO_IDLAN = "IDLAN"
CAMPO_VALOR = "VALORORIGINAL"
CAMPO_DOCUMENTO = "CGCCFO"
CAMPO_NOME = "NOMECFO"
CAMPO_VENCIMENTO = "DATAVENCIMENTO"
CAMPO_EMISSAO = "DATAEMISSAO"
CAMPO_STATUS = "STATUSLAN"

# Margem de renovacao do token. O default do RM e 5 MINUTOS (bem menor
# que a hora tipica de um OAuth2), entao a margem tem que ser curta ou
# o cache nunca serviria para nada.
TOKEN_MARGEM_S = 30
MAX_PAGINAS_PADRAO = 200
PAGE_SIZE_PADRAO = 200


class TotvsError(RuntimeError):
    """Falha generica falando com o RM."""


class TotvsAuthError(TotvsError):
    """Credencial rejeitada (401/403)."""


class TotvsPermissionError(TotvsError):
    """Perfil do usuario restringe a rotina/sentenca (ex.: FE011).

    Existe separado de propósito: uma restricao de perfil NAO pode ser
    confundida com "zero lancamentos no periodo". Ver a nota sobre
    filtro silencioso por coligada em docs/integrations.md.
    """


class TotvsExtractor(Protocol):
    """Contrato minimo -- e so leitura, nao ha metodo de escrita."""

    source: str

    async def fetch_lancamentos(
        self, *, desde: date, ate: date
    ) -> list[dict[str, Any]]: ...

    async def health_check(self) -> bool: ...

    async def aclose(self) -> None: ...

    async def list_coligadas(self) -> set[int] | None: ...


def external_id_rm(codcoligada: Any, idlan: Any) -> str:
    """Chave natural do lancamento na FLAN, usada no `external_id`.

    CONFIRMADO pela TOTVS (Eduarda Soares, 31/08/2026): a combinacao
    que identifica um lancamento de forma unica e CODCOLIGADA + IDLAN.
    O IDLAN e autoincremental no banco, mas o RM exige o vinculo com a
    respectiva coligada.

    CODFILIAL FICOU DE FORA de proposito. A suposicao inicial deste
    adapter era `coligada-filial-idlan`; se a filial de um lancamento
    for corrigida no RM, aquela chave mudaria e o upsert criaria uma
    linha nova em vez de atualizar a existente. A filial continua
    gravada como dado, so nao como identidade.

    Mesmo padrao do PNCP (`cnpj-ano-sequencial`): string estavel para
    upsert com ON CONFLICT.
    """
    return f"{codcoligada}-{idlan}"


def _so_digitos(valor: Any) -> str | None:
    """CNPJ/CPF do FCFO vem formatado ("12.345.678/0001-90").

    Guardamos so-digitos porque e a chave de match com
    `Contrato.contraparte_documento` -- que hoje e `String(32)` livre e
    precisa de normalizacao propria (ticket separado, decisao #8).
    """
    if valor is None:
        return None
    digitos = "".join(c for c in str(valor) if c.isdigit())
    return digitos or None


def _data(valor: Any) -> date | None:
    """RM devolve datas ISO com hora zerada ("2026-09-10T00:00:00")."""
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    try:
        return datetime.fromisoformat(texto).date()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    raise TotvsError(f"data do RM em formato desconhecido: {texto!r}")


def normalize_lancamento(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Envelope unico, identico para REST e ConsultaSQL.

    `valor` passa por `coerce_valor_rm` (que delega ao `coerce_valor`
    reusado do financeiro_contratos): Decimal sempre, float nunca.
    """
    coligada = raw.get(CAMPO_COLIGADA)
    filial = raw.get(CAMPO_FILIAL)
    idlan = raw.get(CAMPO_IDLAN)
    return {
        "external_id": external_id_rm(coligada, idlan),
        "codcoligada": int(coligada) if coligada is not None else None,
        "codfilial": int(filial) if filial is not None else None,
        "idlan": int(idlan) if idlan is not None else None,
        "valor": coerce_valor_rm(raw.get(CAMPO_VALOR)),
        "contraparte_documento": _so_digitos(raw.get(CAMPO_DOCUMENTO)),
        "contraparte_nome": raw.get(CAMPO_NOME),
        "data_vencimento": _data(raw.get(CAMPO_VENCIMENTO)),
        "data_emissao": _data(raw.get(CAMPO_EMISSAO)),
        "status_rm": (
            str(raw[CAMPO_STATUS]) if raw.get(CAMPO_STATUS) is not None else None
        ),
        "source": source,
        "raw": raw,
    }


class _RmHttpBase:
    """Cliente HTTP comum. HTTPS: o ambiente e TCloud (confirmado 24/08)."""

    source = "totvs"

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client = httpx.AsyncClient(
            timeout=timeout, transport=transport, follow_redirects=True
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class RestExtractor(_RmHttpBase):
    """WebAPI REST na `ApiPort`, autenticada por Bearer.

    A propria TOTVS recomenda Bearer no lugar de Basic ("nao e
    recomendada pelo seu baixo nivel de seguranca"). O token sai de
    `POST /api/connect/token` com `{"username","password"}` e dura 5
    min por default -- um pull noturno atravessa o expiry, entao ha
    renovacao por `refresh_token` e um unico retry em 401. Gerar token
    NAO consome licenca, entao renovar no meio do pull e barato.

    Paginacao: `page`/`pageSize` com `hasNext` na resposta. A doc avisa
    que nem toda API de produto implementou paginacao -- se a de
    Financeiro nao tiver, este extractor precisa de ajuste (e vira mais
    um argumento para o ConsultaSQL).
    """

    source = "totvs_rest"

    def __init__(
        self,
        *,
        lancamentos_path: str = LANCAMENTOS_PATH_PADRAO,
        page_size: int = PAGE_SIZE_PADRAO,
        max_paginas: int = MAX_PAGINAS_PADRAO,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._path = lancamentos_path
        self._page_size = page_size
        self._max_paginas = max_paginas
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._expira_em: float = 0.0

    async def _get_token(self) -> str:
        if self._token and self._expira_em - time.time() > TOKEN_MARGEM_S:
            return self._token
        return await self._pedir_token()

    async def _pedir_token(self, *, usar_refresh: bool = False) -> str:
        payload: dict[str, Any]
        if usar_refresh and self._refresh_token:
            payload = {
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            }
        else:
            payload = {"username": self._username, "password": self._password}
        try:
            r = await self._client.post(f"{self._base_url}{TOKEN_PATH}", json=payload)
        except httpx.HTTPError as exc:
            raise TotvsError(f"token RM falhou: {exc}") from exc
        if r.status_code in (401, 403):
            raise TotvsAuthError(f"credenciais RM rejeitadas ({r.status_code})")
        if r.status_code != 200:
            raise TotvsError(f"token {r.status_code}: {r.text[:200]}")
        data = r.json()
        token = data.get("access_token")
        if not token:
            raise TotvsError(f"resposta {TOKEN_PATH} sem access_token: {data}")
        self._token = str(token)
        if data.get("refresh_token"):
            self._refresh_token = str(data["refresh_token"])
        self._expira_em = time.time() + int(data.get("expires_in", 300))
        return self._token

    async def _get(self, url: str, params: dict[str, Any]) -> httpx.Response:
        """GET com um unico retry em 401 (token expirou no meio do pull)."""
        token = await self._get_token()
        r = await self._client.get(
            url, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        if r.status_code == 401:
            token = await self._pedir_token(usar_refresh=True)
            r = await self._client.get(
                url, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        return r

    async def health_check(self) -> bool:
        """Usa `/api/framework/v1/coligadas`, que NAO consome licenca."""
        try:
            r = await self._get(f"{self._base_url}{COLIGADAS_PATH}", {"pageSize": 1})
        except (httpx.HTTPError, TotvsError):
            return False
        return r.status_code == 200

    async def list_coligadas(self) -> set[int] | None:
        """Coligadas visiveis para o perfil do usuario.

        Mesmo endpoint do health_check, que nao consome licenca. Serve
        de guarda contra o filtro silencioso: o RM devolve 200 com
        menos registros quando falta permissao de coligada, entao a
        unica forma de detectar e comparar o que se enxerga com o que
        se espera.
        """
        r = await self._get(
            f"{self._base_url}{COLIGADAS_PATH}", {"pageSize": 200}
        )
        if r.status_code != 200:
            raise TotvsError(f"{COLIGADAS_PATH} {r.status_code}: {r.text[:200]}")
        itens = r.json().get("items") or []
        codigos: set[int] = set()
        for item in itens:
            valor = item.get("codColigada", item.get("CODCOLIGADA"))
            if valor is not None:
                codigos.add(int(valor))
        return codigos

    async def fetch_lancamentos(
        self, *, desde: date, ate: date
    ) -> list[dict[str, Any]]:
        url = f"{self._base_url}{self._path}"
        linhas: list[dict[str, Any]] = []
        for pagina in range(1, self._max_paginas + 1):
            r = await self._get(
                url,
                {
                    # Nomes dos filtros de data A CONFIRMAR com RM
                    # Gestao Financeira.
                    "dataInicial": desde.isoformat(),
                    "dataFinal": ate.isoformat(),
                    "page": pagina,
                    "pageSize": self._page_size,
                },
            )
            if r.status_code == 403:
                raise TotvsPermissionError(
                    f"perfil sem acesso a {self._path} (403): {r.text[:200]}"
                )
            if r.status_code != 200:
                raise TotvsError(f"{self._path} {r.status_code}: {r.text[:200]}")
            corpo = r.json()
            itens = corpo.get("items") or []
            linhas.extend(normalize_lancamento(i, source=self.source) for i in itens)
            if not corpo.get("hasNext"):
                break
        else:
            # Limite de seguranca: `hasNext` sempre True (bug do RM) nao
            # pode virar loop infinito segurando licenca a noite toda.
            logger.warning(
                "totvs: pull parou no limite de %d paginas com hasNext=True",
                self._max_paginas,
            )
        return linhas


class ConsultaSqlExtractor(_RmHttpBase):
    """wsConsultaSQL (SOAP) na `HttpPort`, autenticado por Basic.

    Executa uma sentenca SQL CADASTRADA DENTRO DO RM (codSentenca +
    codColigada + codSistema). Isso reforca o read-only por construcao:
    a query nem mora no nosso codigo. O custo e a dependencia -- mudar
    a extracao vira mudanca no RM (BI > Criacao de consultas SQL), nao
    deploy nosso.

    Sem limite de retorno (confirmado pela TOTVS em 24/08/2026): 1
    requisicao = 1 licenca, mas presa pelo tempo INTEIRO do
    processamento. Sentenca pesada trava uma licenca por minutos.
    """

    source = "totvs_consultasql"

    def __init__(
        self,
        *,
        cod_sentenca: str,
        cod_coligada: int,
        cod_sistema: str,
        path: str = CONSULTASQL_PATH,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._cod_sentenca = cod_sentenca
        self._cod_coligada = cod_coligada
        self._cod_sistema = cod_sistema
        self._path = path

    def _envelope(self, *, desde: date, ate: date) -> str:
        # Parametros multiplos sao separados por ";" (doc TOTVS).
        params = f"DATAINICIAL={desde.isoformat()};DATAFINAL={ate.isoformat()}"
        return (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:tot="http://www.totvs.com/"><soapenv:Header/><soapenv:Body>'
            "<tot:RealizarConsultaSQLContexto>"
            f"<tot:codSentenca>{self._cod_sentenca}</tot:codSentenca>"
            f"<tot:codColigada>{self._cod_coligada}</tot:codColigada>"
            f"<tot:codSistema>{self._cod_sistema}</tot:codSistema>"
            f"<tot:parameters>{params}</tot:parameters>"
            f"<tot:context>CODCOLIGADA={self._cod_coligada}</tot:context>"
            "</tot:RealizarConsultaSQLContexto></soapenv:Body></soapenv:Envelope>"
        )

    async def health_check(self) -> bool:
        return False

    async def list_coligadas(self) -> set[int] | None:
        # A API de coligadas vive na ApiPort (REST); este extractor fala
        # SOAP na HttpPort. Devolver None faz a guarda ser pulada com
        # aviso, em vez de dar um falso "nao enxerga nada".
        return None

    async def fetch_lancamentos(
        self, *, desde: date, ate: date
    ) -> list[dict[str, Any]]:
        try:
            r = await self._client.post(
                f"{self._base_url}{self._path}",
                content=self._envelope(desde=desde, ate=ate).encode("utf-8"),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": "http://www.totvs.com/IwsConsultaSQL/RealizarConsultaSQLContexto",
                },
                auth=(self._username, self._password),
            )
        except httpx.HTTPError as exc:
            raise TotvsError(f"wsConsultaSQL falhou: {exc}") from exc

        if r.status_code in (401, 403):
            raise TotvsAuthError(f"credenciais RM rejeitadas ({r.status_code})")
        if "FE011" in r.text or "filtro por perfil" in r.text:
            raise TotvsPermissionError(
                "sentenca bloqueada por filtro de perfil/usuario (FE011): "
                f"{r.text[:300]}"
            )
        if r.status_code != 200:
            raise TotvsError(f"wsConsultaSQL {r.status_code}: {r.text[:200]}")

        return [
            normalize_lancamento(raw, source=self.source)
            for raw in _parse_dataset(r.text)
        ]


def _parse_dataset(xml_soap: str) -> list[dict[str, Any]]:
    """Extrai o `NewDataSet` que vem em CDATA dentro do envelope SOAP."""
    try:
        envelope = ET.fromstring(xml_soap)
    except ET.ParseError as exc:
        raise TotvsError(f"resposta SOAP nao e XML valido: {exc}") from exc

    interno: str | None = None
    for elemento in envelope.iter():
        if elemento.tag.split("}")[-1].endswith("Result"):
            interno = elemento.text
            break
    if not interno or not interno.strip():
        return []

    try:
        dataset = ET.fromstring(interno)
    except ET.ParseError as exc:
        raise TotvsError(f"DataSet do RM nao e XML valido: {exc}") from exc

    linhas: list[dict[str, Any]] = []
    for resultado in dataset:
        linhas.append(
            {campo.tag.split("}")[-1]: (campo.text or "") for campo in resultado}
        )
    return linhas


class MockExtractor:
    """Mock deterministico -- padrao do repo (Dominio, Onvio, OnSafety...).

    `health_check` retorna False de proposito (decisao #9): o mock
    destrava dev/CI, nao pinta o painel de status de verde. Integracao
    meio-ligada nao passa por conectada.
    """

    source = "totvs_mock"

    async def health_check(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None

    async def list_coligadas(self) -> set[int] | None:
        # As mesmas que o `fetch_lancamentos` fabrica.
        return {1}

    async def fetch_lancamentos(
        self, *, desde: date, ate: date
    ) -> list[dict[str, Any]]:
        # Deterministico a partir da janela: mesma janela, mesmo dado.
        semente = f"{desde.isoformat()}:{ate.isoformat()}"
        linhas = []
        for i in range(1, 6):
            digest = hashlib.sha1(f"{semente}:{i}".encode()).hexdigest()
            idlan = 1000 + int(digest[:4], 16) % 9000
            centavos = int(digest[4:8], 16) % 100
            milhares = int(digest[8:12], 16) % 500
            linhas.append(
                normalize_lancamento(
                    {
                        CAMPO_COLIGADA: 1,
                        CAMPO_FILIAL: 1 + (i % 2),
                        CAMPO_IDLAN: idlan,
                        # String com virgula DE PROPOSITO: o mock exercita
                        # o mesmo caminho de parsing do RM real.
                        CAMPO_VALOR: f"{milhares}.{i}00,{centavos:02d}",
                        CAMPO_DOCUMENTO: f"{digest[:8]}000190",
                        CAMPO_NOME: f"Fornecedor Mock {i}",
                        CAMPO_VENCIMENTO: f"{ate.isoformat()}T00:00:00",
                        CAMPO_EMISSAO: f"{desde.isoformat()}T00:00:00",
                        CAMPO_STATUS: 0,
                    },
                    source=self.source,
                )
            )
        return linhas


__all__ = [
    "ConsultaSqlExtractor",
    "MockExtractor",
    "RestExtractor",
    "TotvsAuthError",
    "TotvsError",
    "TotvsExtractor",
    "TotvsPermissionError",
    "external_id_rm",
    "normalize_lancamento",
]
