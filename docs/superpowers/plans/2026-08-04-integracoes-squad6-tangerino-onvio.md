# Squad 6 — Adapters Tangerino e Onvio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Camada de integração pronta (adapter real + mock determinístico + config) para Tangerino (ponto/Sólides) e Onvio (envio de NF-e ao contador via Domínio/Thomson Reuters), sem endpoints de negócio — squads de mão de obra e fiscal consomem depois.

**Architecture:** Segue o padrão OnSafety do repo: uma classe por integração herdando `IntegrationClient`, mock determinístico embutido (`is_mock` quando falta credencial — NÃO classes separadas de mock; ver `app/integrations/onsafety/client.py`), envelope normalizado `{items, total, page, size, source}` para listagens, erros próprios (`XError`/`XAuthError`), guard de escrita opt-in via Settings.

**Tech Stack:** Python 3.12 async, httpx (`MockTransport` nos testes), pydantic-settings, pytest + pytest-asyncio. Zero dependências novas.

## Global Constraints

- Docstrings e comentários em PT-BR **sem acentos** (convenção do repo — ver onsafety/client.py: "deterministico", "producao").
- Todos os adapters herdam `app.integrations.base.IntegrationClient` e implementam `health_check()`.
- Mock determinístico: mesmo input → mesmo output, entre processos (hash sha1, nunca random/tempo).
- Rodar testes: `cd apps/api && uv run --extra dev pytest <arquivo> -v`. Lint: `uv run ruff check app tests` e `uv run ruff format --check app tests`.
- Nenhum segredo em código; tudo via `Settings` (`app/core/config.py`).
- CPFs de mock devem ter dígitos verificadores VÁLIDOS (helper `_mock_cpf`, copiado do padrão onsafety — `is_valid_cpf` do matching descartaria CPFs inválidos em silêncio).
- A suite inteira (565+ testes) deve continuar verde ao final: `uv run --extra dev pytest`.

## Fatos de API validados (não inventar além disto)

**Tangerino** — spec Swagger 2.0 público baixado de `https://employer.tangerino.com.br/v2/api-docs` em 2026-08-04 (cópia em scratchpad `tangerino-api.json`):

| Uso | Endpoint | Resposta |
|---|---|---|
| Listar funcionários | `GET /employee/find-all` (query `pageNumber`, `pageSize`, `showFired`) | `Page«EmployeeReturnWithoutPinDTO»` — página Spring (`content`, `totalElements`) |
| Um funcionário | `GET /employee/find` (query `externalId` ou `tangerinoId`) | `EmployeeReturnWithoutPinDTO` |
| Batidas por funcionário | `GET /external/api/v1/payssego/punches/{employeeId}` (query `startDate`, `endDate`, `pageNumber`, `pageSize`) | `Page«PunchSimpleDTO»` |
| Locais de trabalho | `GET /workplace/find-all` | `Page«WorkplaceReturnDTO»` `{id, externalId, name, active, standard}` |

- Auth: apiKey no header `Authorization` (securityDefinition "Token Access"). Sem prefixo `Bearer` no spec — se a API real exigir, ajustar só o `_auth_headers`.
- `PunchSimpleDTO` = `{dateWorked:int, employeeId:int, employeeExternalId:str, startDateTimestamp:int, endDateTimestamp:int, pis:str, status:str, workedTimeInSeconds:int}`. **NÃO existe latitude/longitude em batida nem em lugar nenhum do spec.** O vínculo funcionário→obra é via `workplace` (funcionário tem `workplaceList`/`currentWorkplaceDTO`). Registrar isso no doc (Task 7) — a hipótese "localização da batida" do docx de mão de obra precisa ser confirmada com o suporte Sólides; o caminho garantido é workplace→obra.
- **Não existe endpoint de afastamentos** no spec público (`leave|vacation|ferias` = zero hits). `list_afastamentos` fica fora do client real; ver decisão na Task 3.
- Formato de `startDate`/`endDate` e unidade dos timestamps (epoch ms assumido) não são declarados no spec — marcar com comentário `a confirmar com credencial real` e expor os ints crus.

**Onvio** — fluxo do script de referência da comunidade (fornecido pelo cliente), a validar com credencial real:

1. `POST https://auth.thomsonreuters.com/oauth/token` — form-encoded `grant_type=client_credentials`, `client_id`, `client_secret`, `audience` (default `409f91f6-dc17-44c8-a5d8-e0a1bafd8b67`) → `{access_token, expires_in?}`. Token dura ~24h → cache em memória com lock (padrão `DominioClient._get_token`), NUNCA em arquivo. O cookie hardcoded do script é lixo de tutorial — não reproduzir.
2. `GET https://api.onvio.com.br/dominio/integration/v1/activation/info` — headers `Authorization: Bearer` + `x-integration-key` → `{accountantOfficeNationalIdentity, clientNationalIdentity}`.
3. `POST .../integration/v1/activation/enable` → `{integrationKey}` — chave de sessão usada nos calls de invoice (cachear em memória junto do token).
4. `POST https://api.onvio.com.br/dominio/invoice/v3/batches` — multipart `file[]` = XML + `query` = `{"boxe/File": false}` → `{id}`.
5. `GET .../invoice/v3/batches/{id}` → sucesso quando `filesExpanded[0].apiStatus.message == "Arquivo armazenado na API"`.

Nota de arquitetura: já existe `app/integrations/dominio/client.py` (Central do Desenvolvedor, `api.dominioexterior.com.br`) para o mesmo objetivo por outra superfície de API. Os dois coexistem; qual o service de fiscal usa é decisão da squad de fiscal, fora deste escopo.

## File Structure

- Create: `apps/api/app/integrations/tangerino/__init__.py` — re-exporta client/erros
- Create: `apps/api/app/integrations/tangerino/client.py` — `TangerinoClient` (real + mock embutido)
- Create: `apps/api/app/integrations/onvio/__init__.py` — re-exporta client/erros
- Create: `apps/api/app/integrations/onvio/client.py` — `OnvioClient` (real + mock embutido)
- Modify: `apps/api/app/core/config.py` — settings `tangerino_*` e `onvio_*` (depois do bloco `onsafety_*`, ~linha 156)
- Test: `apps/api/tests/test_tangerino_client.py`
- Test: `apps/api/tests/test_onvio_client.py`
- Modify: `docs/integrations.md` — status das duas integrações

---

### Task 1: Settings Tangerino + Onvio

**Files:**
- Modify: `apps/api/app/core/config.py` (inserir após `onsafety_projeto_id`, antes de `@lru_cache`)
- Test: `apps/api/tests/test_tangerino_client.py` (novo, só o teste de settings por ora)

**Interfaces:**
- Produces: `Settings.tangerino_api_key: str | None`, `Settings.tangerino_base_url: str`, `Settings.onvio_client_id: str | None`, `Settings.onvio_client_secret: str | None`, `Settings.onvio_integration_key: str | None`, `Settings.onvio_audience: str`, `Settings.onvio_allow_send: bool` — consumidos pelas factories/DI das squads consumidoras e pelos defaults dos clients.

- [ ] **Step 1: Write the failing test**

```python
"""Testes do adapter Tangerino (ponto/Solides)."""
from __future__ import annotations

from app.core.config import Settings


def test_settings_defaults_tangerino_e_onvio():
    s = Settings(_env_file=None)
    assert s.tangerino_api_key is None
    assert s.tangerino_base_url == "https://employer.tangerino.com.br"
    assert s.onvio_client_id is None
    assert s.onvio_client_secret is None
    assert s.onvio_integration_key is None
    assert s.onvio_audience == "409f91f6-dc17-44c8-a5d8-e0a1bafd8b67"
    assert s.onvio_allow_send is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run --extra dev pytest tests/test_tangerino_client.py -v`
Expected: FAIL com `AttributeError: 'Settings' object has no attribute 'tangerino_api_key'` (ou ValidationError equivalente)

- [ ] **Step 3: Write minimal implementation**

Em `apps/api/app/core/config.py`, logo após o campo `onsafety_projeto_id` (mantendo o estilo dos blocos vizinhos):

```python
    # Tangerino/Solides (ponto eletronico -- apropriacao de mao de obra
    # e onboarding, Modulo A/B). Sem api key o adapter cai em mock
    # deterministico -- mesmo padrao OnSafety/Dominio. Auth e apiKey
    # crua no header Authorization (spec v2/api-docs, 2026-08-04).
    tangerino_api_key: str | None = Field(default=None)
    tangerino_base_url: str = Field(
        default="https://employer.tangerino.com.br"
    )

    # Onvio (Dominio/Thomson Reuters -- envio de NF-e ao contador,
    # Modulo C). Sem qualquer uma das 3 credenciais o adapter cai em
    # mock deterministico. `onvio_audience` e fixo da Thomson Reuters
    # (script de referencia da comunidade).
    onvio_client_id: str | None = Field(default=None)
    onvio_client_secret: str | None = Field(default=None)
    onvio_integration_key: str | None = Field(default=None)
    onvio_audience: str = Field(
        default="409f91f6-dc17-44c8-a5d8-e0a1bafd8b67"
    )
    # Guard-rail: enviar NF-e e ESCRITA no sistema contabil de producao
    # do escritorio parceiro (nao ha sandbox conhecido). Envio real so
    # com opt-in explicito; mock nao e afetado. Mesmo espirito do
    # ONSAFETY_ALLOW_PROD_WRITE.
    onvio_allow_send: bool = Field(default=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run --extra dev pytest tests/test_tangerino_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/core/config.py apps/api/tests/test_tangerino_client.py
git commit -m "feat(integracoes): settings Tangerino e Onvio com guard de envio"
```

---

### Task 2: TangerinoClient — modo mock determinístico

**Files:**
- Create: `apps/api/app/integrations/tangerino/__init__.py`
- Create: `apps/api/app/integrations/tangerino/client.py`
- Test: `apps/api/tests/test_tangerino_client.py` (append)

**Interfaces:**
- Consumes: `app.integrations.base.IntegrationClient`, `app.core.cpf.normalize_cpf`
- Produces (contrato para a squad de mão de obra):
  - `TangerinoClient(api_token: str | None = None, base_url: str = TANGERINO_BASE_URL, client: httpx.AsyncClient | None = None, timeout: float = 30.0)`
  - `TangerinoClient.is_mock: bool` (True sem token)
  - `async list_funcionarios(*, page: int = 0, size: int = 100, incluir_demitidos: bool = False) -> dict` — envelope `{items, total, page, size, source}`; item `{id:int, external_id:str|None, nome:str, cpf:str|None (11 digitos), pis:str|None, admissao:str|None (ISO), demitido:bool, workplaces:[{id:int, external_id:str|None, nome:str}]}`
  - `async list_batidas(employee_id: int, *, start_date: str, end_date: str, page: int = 0, size: int = 100) -> dict` — item `{employee_id:int, employee_external_id:str|None, data_trabalho_ts:int|None, inicio_ts:int|None, fim_ts:int|None, segundos_trabalhados:int|None, status:str|None, pis:str|None}` (timestamps crus da API, epoch assumido)
  - `async list_locais_trabalho(*, page: int = 0, size: int = 100) -> dict` — item `{id:int, external_id:str|None, nome:str, ativo:bool, padrao:bool}`
  - `TangerinoError`, `TangerinoAuthError`

- [ ] **Step 1: Write the failing tests** (append em `tests/test_tangerino_client.py`)

```python
import pytest

from app.integrations.tangerino.client import TangerinoClient

# --- mock (sem token) ------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_funcionarios_deterministico():
    c1 = TangerinoClient(api_token=None)
    c2 = TangerinoClient(api_token="")
    r1 = await c1.list_funcionarios()
    r2 = await c2.list_funcionarios()
    assert r1 == r2
    assert r1["source"] == "tangerino_mock"
    assert r1["total"] == 3
    assert len(r1["items"]) == 3


@pytest.mark.asyncio
async def test_mock_funcionarios_cpf_valido_e_workplaces_de_duas_obras():
    from app.core.cpf import is_valid_cpf

    c = TangerinoClient(api_token=None)
    r = await c.list_funcionarios()
    workplaces = set()
    for item in r["items"]:
        assert is_valid_cpf(item["cpf"])
        assert item["workplaces"], "funcionario mock sem workplace"
        workplaces.add(item["workplaces"][0]["nome"])
    assert len(workplaces) == 2  # 2 obras distintas no dataset


@pytest.mark.asyncio
async def test_mock_batidas_por_funcionario_e_estavel():
    c = TangerinoClient(api_token=None)
    func = (await c.list_funcionarios())["items"][0]
    r1 = await c.list_batidas(
        func["id"], start_date="2026-08-01", end_date="2026-08-05"
    )
    r2 = await c.list_batidas(
        func["id"], start_date="2026-08-01", end_date="2026-08-05"
    )
    assert r1 == r2
    assert r1["source"] == "tangerino_mock"
    assert r1["total"] > 0
    b = r1["items"][0]
    assert b["employee_id"] == func["id"]
    assert b["fim_ts"] > b["inicio_ts"]
    assert b["segundos_trabalhados"] > 0


@pytest.mark.asyncio
async def test_mock_locais_trabalho():
    c = TangerinoClient(api_token=None)
    r = await c.list_locais_trabalho()
    assert r["total"] == 2
    nomes = {w["nome"] for w in r["items"]}
    assert nomes == {"OBRA BR-040 LOTE 3", "OBRA MG-050 RECAPEAMENTO"}


@pytest.mark.asyncio
async def test_mock_health_check_true():
    c = TangerinoClient(api_token=None)
    assert await c.health_check() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run --extra dev pytest tests/test_tangerino_client.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.integrations.tangerino'`

- [ ] **Step 3: Write the implementation**

`apps/api/app/integrations/tangerino/__init__.py`:

```python
from app.integrations.tangerino.client import (
    TangerinoAuthError,
    TangerinoClient,
    TangerinoError,
)

__all__ = ["TangerinoAuthError", "TangerinoClient", "TangerinoError"]
```

`apps/api/app/integrations/tangerino/client.py`:

```python
"""Tangerino/Solides -- ponto eletronico (Modulos A e B).

O Tangerino (hoje Solides Ponto) guarda funcionarios, batidas de ponto
e locais de trabalho (workplaces). E a fonte para a apropriacao de mao
de obra por obra: o vinculo funcionario -> obra vem do WORKPLACE do
funcionario (workplaceList/currentWorkplaceDTO).

Endpoints usados (validados contra o spec publico
https://employer.tangerino.com.br/v2/api-docs em 2026-08-04):

- `GET /employee/find-all`                              -- funcionarios
- `GET /external/api/v1/payssego/punches/{employeeId}`  -- batidas
- `GET /workplace/find-all`                             -- locais de trabalho

Particularidades do spec:

- Auth e apiKey CRUA no header `Authorization` (securityDefinition
  "Token Access", sem prefixo Bearer). Se a API real exigir prefixo,
  ajustar apenas `_auth_headers`.
- Paginas sao Spring Data (`content`, `totalElements`), como OnSafety.
- `PunchSimpleDTO` NAO tem geolocalizacao -- nem nenhum outro modelo
  do spec publico. Nao ha endpoint de afastamentos. A unidade dos
  timestamps (epoch ms assumido) e o formato de startDate/endDate
  ("yyyy-MM-dd" assumido) nao sao declarados no spec -- a confirmar
  com credencial real; por isso os timestamps sao expostos CRUS.

Padrao do projeto: sem token (`TANGERINO_API_KEY` vazio), o adapter cai
num mock deterministico -- mesmo padrao OnSafety/Dominio/OneDrive.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

TANGERINO_BASE_URL = "https://employer.tangerino.com.br"


def _mock_cpf(digest: str) -> str:
    """CPF deterministico com digitos verificadores VALIDOS.

    Mesmo racional do mock OnSafety: consumidores validam CPF antes de
    casar com dp_employees; CPF invalido descartaria o dataset em
    silencio.
    """
    base = [int(d) for d in str(int(digest[:12], 16) % 10**9).zfill(9)]
    resto = sum(d * (10 - i) for i, d in enumerate(base)) % 11
    base.append(0 if resto < 2 else 11 - resto)
    resto = sum(d * (11 - i) for i, d in enumerate(base)) % 11
    base.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, base))


class TangerinoError(RuntimeError):
    """Transporte, status nao-2xx ou payload inesperado do Tangerino."""


class TangerinoAuthError(TangerinoError):
    """401/403 -- api key invalida ou sem permissao."""


# Dataset mock: 2 obras e 3 funcionarios (2 na obra A, 1 na obra B) --
# suficiente para a squad de mao de obra testar rateio por obra.
_MOCK_WORKPLACES = [
    {"id": 9001, "external_id": "OBRA-BR040-L3", "nome": "OBRA BR-040 LOTE 3",
     "ativo": True, "padrao": True},
    {"id": 9002, "external_id": "OBRA-MG050-RC", "nome": "OBRA MG-050 RECAPEAMENTO",
     "ativo": True, "padrao": False},
]
_MOCK_FUNCIONARIOS_NOMES = [
    ("JOSE DA SILVA", 0),      # obra A
    ("MARIA SOUZA", 0),        # obra A
    ("CARLOS SANTOS", 1),      # obra B
]


class TangerinoClient(IntegrationClient):
    name = "tangerino"

    def __init__(
        self,
        *,
        api_token: str | None = None,
        base_url: str = TANGERINO_BASE_URL,
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

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        if self.is_mock:
            return True
        try:
            await self.list_funcionarios(page=0, size=1)
        except TangerinoError:
            return False
        return True

    # ------------------------------ pull ---------------------------------

    async def list_funcionarios(
        self,
        *,
        page: int = 0,
        size: int = 100,
        incluir_demitidos: bool = False,
    ) -> dict[str, Any]:
        """Lista funcionarios do empregador.

        Item: {id, external_id, nome, cpf (11 digitos), pis, admissao,
               demitido, workplaces: [{id, external_id, nome}]}
        """
        if self.is_mock:
            return self._mock_funcionarios(page, size, incluir_demitidos)
        raise NotImplementedError  # Task 3

    async def list_batidas(
        self,
        employee_id: int,
        *,
        start_date: str,
        end_date: str,
        page: int = 0,
        size: int = 100,
    ) -> dict[str, Any]:
        """Lista batidas de ponto de um funcionario no periodo.

        Datas em ISO `YYYY-MM-DD`. Timestamps expostos CRUS (epoch
        assumido -- unidade a confirmar com credencial real).

        Item: {employee_id, employee_external_id, data_trabalho_ts,
               inicio_ts, fim_ts, segundos_trabalhados, status, pis}
        """
        if self.is_mock:
            return self._mock_batidas(
                employee_id, start_date, end_date, page, size
            )
        raise NotImplementedError  # Task 3

    async def list_locais_trabalho(
        self, *, page: int = 0, size: int = 100
    ) -> dict[str, Any]:
        """Lista locais de trabalho (workplaces) -- vinculo com obras.

        Item: {id, external_id, nome, ativo, padrao}
        """
        if self.is_mock:
            return self._page_mock(list(_MOCK_WORKPLACES), page, size)
        raise NotImplementedError  # Task 3

    # ------------------------------ mock ---------------------------------

    def _page_mock(
        self, items: list[dict[str, Any]], page: int, size: int
    ) -> dict[str, Any]:
        start = page * size
        return {
            "items": items[start : start + size],
            "total": len(items),
            "page": page,
            "size": size,
            "source": "tangerino_mock",
        }

    def _mock_funcionarios(
        self, page: int, size: int, incluir_demitidos: bool
    ) -> dict[str, Any]:
        del incluir_demitidos  # dataset mock nao tem demitidos
        items = []
        for i, (nome, wp_idx) in enumerate(_MOCK_FUNCIONARIOS_NOMES):
            digest = hashlib.sha1(f"tangerino|func|{i}".encode()).hexdigest()
            wp = _MOCK_WORKPLACES[wp_idx]
            items.append(
                {
                    "id": 100 + i,
                    "external_id": f"EXT-{1000 + i}",
                    "nome": nome,
                    "cpf": _mock_cpf(digest),
                    "pis": str(12000000000 + int(digest[:6], 16)),
                    "admissao": f"202{i % 3}-03-01",
                    "demitido": False,
                    "workplaces": [
                        {
                            "id": wp["id"],
                            "external_id": wp["external_id"],
                            "nome": wp["nome"],
                        }
                    ],
                }
            )
        return self._page_mock(items, page, size)

    def _mock_batidas(
        self,
        employee_id: int,
        start_date: str,
        end_date: str,
        page: int,
        size: int,
    ) -> dict[str, Any]:
        # 2 batidas por funcionario/consulta, funcao apenas de
        # (employee_id, start_date): deterministico e estavel.
        digest = hashlib.sha1(
            f"tangerino|punch|{employee_id}|{start_date}".encode()
        ).hexdigest()
        del end_date
        base_ts = 1_754_000_000_000 + (int(digest[:6], 16) % 86_400_000)
        items = []
        for j in range(2):
            inicio = base_ts + j * 43_200_000  # +12h por batida
            trabalhado = 4 * 3600 + (int(digest[6 + j], 16) % 3600)
            items.append(
                {
                    "employee_id": employee_id,
                    "employee_external_id": f"EXT-{900 + employee_id}",
                    "data_trabalho_ts": inicio,
                    "inicio_ts": inicio,
                    "fim_ts": inicio + trabalhado * 1000,
                    "segundos_trabalhados": trabalhado,
                    "status": "CLOSED",
                    "pis": None,
                }
            )
        return self._page_mock(items, page, size)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_tangerino_client.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/integrations/tangerino/ apps/api/tests/test_tangerino_client.py
git commit -m "feat(tangerino): adapter com mock deterministico (funcionarios, batidas, workplaces)"
```

---

### Task 3: TangerinoClient — HTTP real + normalização

**Files:**
- Modify: `apps/api/app/integrations/tangerino/client.py` (substituir os `raise NotImplementedError`)
- Test: `apps/api/tests/test_tangerino_client.py` (append)

**Interfaces:**
- Consumes: assinaturas da Task 2 (inalteradas)
- Produces: mesmos métodos operando contra a API real; `app.core.cpf.normalize_cpf` aplicado ao CPF

- [ ] **Step 1: Write the failing tests** (append)

```python
from httpx import AsyncClient, MockTransport, Request, Response

from app.integrations.tangerino.client import (
    TangerinoAuthError,
    TangerinoError,
)


def _real_tangerino(handler):
    transport = MockTransport(handler)
    http = AsyncClient(
        transport=transport, base_url="https://employer.tangerino.com.br"
    )
    return TangerinoClient(api_token="tok-123", client=http)


def _spring(content, total):
    return Response(200, json={"content": content, "totalElements": total})


@pytest.mark.asyncio
async def test_real_list_funcionarios_normaliza_e_auth_header():
    seen = {}

    def handler(request: Request) -> Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return _spring(
            [
                {
                    "id": 7,
                    "externalId": "E-7",
                    "name": "JOSE DA SILVA",
                    "cpf": "529.982.247-25",
                    "pis": "12345678901",
                    "admissionDate": "2024-02-01",
                    "fired": False,
                    "workplaceList": [
                        {"id": 1, "externalId": "OB-1", "name": "OBRA X"}
                    ],
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_funcionarios(page=0, size=50)
    assert seen["auth"] == "tok-123"
    assert seen["path"] == "/employee/find-all"
    assert seen["params"]["pageNumber"] == "0"
    assert seen["params"]["pageSize"] == "50"
    item = r["items"][0]
    assert item["cpf"] == "52998224725"
    assert item["workplaces"] == [
        {"id": 1, "external_id": "OB-1", "nome": "OBRA X"}
    ]
    assert r["total"] == 1
    assert r["source"] == "tangerino"


@pytest.mark.asyncio
async def test_real_list_batidas_path_e_periodo():
    seen = {}

    def handler(request: Request) -> Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return _spring(
            [
                {
                    "employeeId": 7,
                    "employeeExternalId": "E-7",
                    "dateWorked": 1754006400000,
                    "startDateTimestamp": 1754032800000,
                    "endDateTimestamp": 1754065200000,
                    "workedTimeInSeconds": 28800,
                    "status": "CLOSED",
                    "pis": "12345678901",
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_batidas(7, start_date="2026-08-01", end_date="2026-08-05")
    assert seen["path"] == "/external/api/v1/payssego/punches/7"
    assert seen["params"]["startDate"] == "2026-08-01"
    assert seen["params"]["endDate"] == "2026-08-05"
    b = r["items"][0]
    assert b["segundos_trabalhados"] == 28800
    assert b["inicio_ts"] == 1754032800000
    assert r["source"] == "tangerino"


@pytest.mark.asyncio
async def test_real_401_vira_auth_error():
    def handler(request: Request) -> Response:
        return Response(401, json={"error": "Unauthorized"})

    c = _real_tangerino(handler)
    with pytest.raises(TangerinoAuthError):
        await c.list_funcionarios()


@pytest.mark.asyncio
async def test_real_500_vira_tangerino_error():
    def handler(request: Request) -> Response:
        return Response(500, text="boom")

    c = _real_tangerino(handler)
    with pytest.raises(TangerinoError):
        await c.list_locais_trabalho()


@pytest.mark.asyncio
async def test_real_locais_trabalho_normaliza():
    def handler(request: Request) -> Response:
        assert request.url.path == "/workplace/find-all"
        return _spring(
            [
                {
                    "id": 1,
                    "externalId": "OB-1",
                    "name": "OBRA X",
                    "active": True,
                    "standard": False,
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_locais_trabalho()
    assert r["items"] == [
        {
            "id": 1,
            "external_id": "OB-1",
            "nome": "OBRA X",
            "ativo": True,
            "padrao": False,
        }
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run --extra dev pytest tests/test_tangerino_client.py -v -k real`
Expected: FAIL com `NotImplementedError`

- [ ] **Step 3: Write the implementation** — substituir os três `raise NotImplementedError` e acrescentar os privados:

```python
    # (em list_funcionarios, no lugar do NotImplementedError)
        raw = await self._get_page(
            "/employee/find-all",
            page=page,
            size=size,
            showFired=1 if incluir_demitidos else 0,
        )
        return self._envelope(raw, page, size, self._normalize_funcionario)

    # (em list_batidas, no lugar do NotImplementedError)
        raw = await self._get_page(
            f"/external/api/v1/payssego/punches/{employee_id}",
            page=page,
            size=size,
            startDate=start_date,
            endDate=end_date,
        )
        return self._envelope(raw, page, size, self._normalize_batida)

    # (em list_locais_trabalho, no lugar do NotImplementedError)
        raw = await self._get_page("/workplace/find-all", page=page, size=size)
        return self._envelope(raw, page, size, self._normalize_workplace)

    # --------------------------- HTTP interno ----------------------------

    def _auth_headers(self) -> dict[str, str]:
        # apiKey CRUA no header Authorization (spec "Token Access") --
        # sem prefixo Bearer; se a API real exigir, ajustar so aqui.
        return {"Authorization": self._api_token}

    async def _get_page(
        self, endpoint: str, *, page: int, size: int, **params: Any
    ) -> dict[str, Any]:
        # Paginacao: o spec lista page/pageNumber/offset/size/pageSize
        # sem documentar qual vale; pageNumber/pageSize e o par usado
        # pelos exemplos payssego -- a confirmar com credencial real.
        query = {"pageNumber": page, "pageSize": size, **params}
        try:
            r = await self._client.get(
                endpoint, params=query, headers=self._auth_headers()
            )
        except httpx.HTTPError as exc:
            raise TangerinoError(
                f"transporte Tangerino ({endpoint}): {exc}"
            ) from exc
        if r.status_code in (401, 403):
            raise TangerinoAuthError(
                f"Tangerino {endpoint} status {r.status_code}: "
                f"api key invalida ou sem permissao"
            )
        if r.status_code >= 300:
            raise TangerinoError(
                f"Tangerino {endpoint} status {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise TangerinoError(
                f"resposta nao-JSON Tangerino ({endpoint}): {exc}"
            ) from exc
        if not isinstance(data, dict) or "content" not in data:
            raise TangerinoError(
                f"Tangerino {endpoint}: pagina Spring esperada, veio "
                f"{type(data).__name__}"
            )
        return data

    def _envelope(
        self, raw: dict[str, Any], page: int, size: int, normalize: Any
    ) -> dict[str, Any]:
        return {
            "items": [normalize(item) for item in raw.get("content") or []],
            "total": int(raw.get("totalElements") or 0),
            "page": page,
            "size": size,
            "source": "tangerino",
        }

    @staticmethod
    def _normalize_funcionario(raw: dict[str, Any]) -> dict[str, Any]:
        from app.core.cpf import normalize_cpf

        return {
            "id": raw.get("id"),
            "external_id": raw.get("externalId"),
            "nome": raw.get("name"),
            "cpf": normalize_cpf(raw.get("cpf") or "") or None,
            "pis": raw.get("pis"),
            "admissao": raw.get("admissionDate"),
            "demitido": bool(raw.get("fired")),
            "workplaces": [
                {
                    "id": wp.get("id"),
                    "external_id": wp.get("externalId"),
                    "nome": wp.get("name"),
                }
                for wp in raw.get("workplaceList") or []
            ],
        }

    @staticmethod
    def _normalize_batida(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "employee_id": raw.get("employeeId"),
            "employee_external_id": raw.get("employeeExternalId"),
            "data_trabalho_ts": raw.get("dateWorked"),
            "inicio_ts": raw.get("startDateTimestamp"),
            "fim_ts": raw.get("endDateTimestamp"),
            "segundos_trabalhados": raw.get("workedTimeInSeconds"),
            "status": raw.get("status"),
            "pis": raw.get("pis"),
        }

    @staticmethod
    def _normalize_workplace(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": raw.get("id"),
            "external_id": raw.get("externalId"),
            "nome": raw.get("name"),
            "ativo": bool(raw.get("active")),
            "padrao": bool(raw.get("standard")),
        }
```

Mover o import de `normalize_cpf` para o topo do arquivo (`from app.core.cpf import normalize_cpf`) e remover o import local do método — o import local acima é só para o snippet ficar autocontido.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_tangerino_client.py -v`
Expected: PASS (todos, mock + real)

- [ ] **Step 5: Lint e commit**

```bash
cd apps/api && uv run ruff check app/integrations/tangerino tests/test_tangerino_client.py && uv run ruff format app/integrations/tangerino tests/test_tangerino_client.py
git add apps/api/app/integrations/tangerino/ apps/api/tests/test_tangerino_client.py
git commit -m "feat(tangerino): client HTTP real com normalizacao e erros tipados"
```

---

### Task 4: OnvioClient — modo mock determinístico

**Files:**
- Create: `apps/api/app/integrations/onvio/__init__.py`
- Create: `apps/api/app/integrations/onvio/client.py`
- Test: `apps/api/tests/test_onvio_client.py` (novo)

**Interfaces:**
- Consumes: `app.integrations.base.IntegrationClient`
- Produces (contrato para a squad de fiscal):
  - `OnvioClient(client_id: str | None = None, client_secret: str | None = None, integration_key: str | None = None, audience: str = ONVIO_DEFAULT_AUDIENCE, allow_send: bool = False, client: httpx.AsyncClient | None = None, timeout: float = 60.0)`
  - `OnvioClient.is_mock: bool` (True se qualquer credencial faltar)
  - `async check_activation() -> dict` — `{escritorio_cnpj:str, cliente_cnpj:str, source:str}`
  - `async send_nfe_xml(*, filename: str, content: bytes) -> dict` — `{batch_id:str, source:str}`
  - `async get_batch_status(batch_id: str) -> dict` — `{batch_id:str, stored:bool, message:str, source:str}`
  - `OnvioError`, `OnvioAuthError`, `OnvioSendBlockedError`

- [ ] **Step 1: Write the failing tests**

```python
"""Testes do adapter Onvio (Dominio/Thomson Reuters -- NF-e)."""
from __future__ import annotations

import pytest

from app.integrations.onvio.client import (
    OnvioClient,
    OnvioError,
    OnvioSendBlockedError,
)

XML = b"<?xml version='1.0'?><NFe><infNFe Id='NFe123'/></NFe>"


@pytest.mark.asyncio
async def test_mock_send_deterministico():
    c1 = OnvioClient()
    c2 = OnvioClient(client_id="", client_secret=None, integration_key=None)
    r1 = await c1.send_nfe_xml(filename="nf.xml", content=XML)
    r2 = await c2.send_nfe_xml(filename="nf.xml", content=XML)
    assert r1 == r2
    assert r1["source"] == "onvio_mock"
    assert r1["batch_id"].startswith("mock-")


@pytest.mark.asyncio
async def test_mock_send_distingue_conteudos():
    c = OnvioClient()
    r1 = await c.send_nfe_xml(filename="a.xml", content=XML)
    r2 = await c.send_nfe_xml(filename="a.xml", content=XML + b"<!-- x -->")
    assert r1["batch_id"] != r2["batch_id"]


@pytest.mark.asyncio
async def test_mock_status_sempre_armazenado():
    c = OnvioClient()
    r = await c.send_nfe_xml(filename="nf.xml", content=XML)
    st = await c.get_batch_status(r["batch_id"])
    assert st["stored"] is True
    assert st["message"] == "Arquivo armazenado na API"
    assert st["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_check_activation():
    c = OnvioClient()
    info = await c.check_activation()
    assert len(info["escritorio_cnpj"]) == 14
    assert len(info["cliente_cnpj"]) == 14
    assert info["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_ignora_guard_allow_send():
    # Guard so vale para envio REAL; mock sempre permitido.
    c = OnvioClient(allow_send=False)
    r = await c.send_nfe_xml(filename="nf.xml", content=XML)
    assert r["source"] == "onvio_mock"


@pytest.mark.asyncio
async def test_mock_health_check_true():
    assert await OnvioClient().health_check() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run --extra dev pytest tests/test_onvio_client.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.integrations.onvio'`

- [ ] **Step 3: Write the implementation**

`apps/api/app/integrations/onvio/__init__.py`:

```python
from app.integrations.onvio.client import (
    OnvioAuthError,
    OnvioClient,
    OnvioError,
    OnvioSendBlockedError,
)

__all__ = [
    "OnvioAuthError",
    "OnvioClient",
    "OnvioError",
    "OnvioSendBlockedError",
]
```

`apps/api/app/integrations/onvio/client.py` (nesta task, só o esqueleto + mock; HTTP real fica `NotImplementedError` até as Tasks 5-6):

```python
"""Onvio -- Dominio/Thomson Reuters: envio de NF-e ao contador (Modulo C).

Fluxo (script de referencia da comunidade, a validar com credencial):

1. Token OAuth2 client_credentials em auth.thomsonreuters.com (dura
   ~24h -- cache em memoria com lock, NUNCA em arquivo).
2. `GET  /dominio/integration/v1/activation/info`   -- CNPJs do vinculo
3. `POST /dominio/integration/v1/activation/enable` -- integrationKey
   de sessao (usada nos calls de invoice; cacheada em memoria).
4. `POST /dominio/invoice/v3/batches`      -- multipart file[] + query
5. `GET  /dominio/invoice/v3/batches/{id}` -- sucesso quando
   filesExpanded[0].apiStatus.message == "Arquivo armazenado na API"

Guard-rail: enviar NF-e e ESCRITA no Dominio de producao do escritorio
contabil (sem sandbox conhecido). Envio real exige allow_send=True
(ONVIO_ALLOW_SEND no env); mock nao e afetado.

Nota: `app/integrations/dominio` fala com a Central do Desenvolvedor
(api.dominioexterior.com.br) -- outra superficie para o mesmo objetivo.
Os dois coexistem; a escolha e da camada de servico do fiscal.

Padrao do projeto: sem qualquer uma das 3 credenciais, o adapter cai
num mock deterministico -- mesmo padrao OnSafety/Dominio/OneDrive.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

import httpx

from app.integrations.base import IntegrationClient

logger = logging.getLogger(__name__)

ONVIO_AUTH_URL = "https://auth.thomsonreuters.com/oauth/token"
ONVIO_API_BASE = "https://api.onvio.com.br"
ONVIO_DEFAULT_AUDIENCE = "409f91f6-dc17-44c8-a5d8-e0a1bafd8b67"
_STORED_MESSAGE = "Arquivo armazenado na API"


class OnvioError(RuntimeError):
    """Transporte, status nao-2xx ou payload inesperado do Onvio."""


class OnvioAuthError(OnvioError):
    """Credenciais rejeitadas (token ou integration key)."""


class OnvioSendBlockedError(OnvioError):
    """Envio real de NF-e bloqueado por guard-rail (ONVIO_ALLOW_SEND)."""


class OnvioClient(IntegrationClient):
    name = "onvio"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        integration_key: str | None = None,
        audience: str = ONVIO_DEFAULT_AUDIENCE,
        allow_send: bool = False,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._client_id = client_id or ""
        self._client_secret = client_secret or ""
        self._integration_key = integration_key or ""
        self._audience = audience
        self._allow_send = allow_send
        self._own_client = client is None
        # Sem base_url: o client fala com DOIS hosts (auth.thomsonreuters
        # e api.onvio) -- URLs sempre absolutas.
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._activation_key: str | None = None
        self._token_lock = asyncio.Lock()

    @property
    def is_mock(self) -> bool:
        return not (
            self._client_id and self._client_secret and self._integration_key
        )

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        if self.is_mock:
            return True
        try:
            await self.check_activation()
        except OnvioError:
            return False
        return True

    # ---------------------------- operacoes ------------------------------

    async def check_activation(self) -> dict[str, Any]:
        """CNPJs do vinculo escritorio <-> cliente na ativacao."""
        if self.is_mock:
            return {
                "escritorio_cnpj": "11222333000181",
                "cliente_cnpj": "99888777000162",
                "source": "onvio_mock",
            }
        raise NotImplementedError  # Task 5

    async def send_nfe_xml(
        self, *, filename: str, content: bytes
    ) -> dict[str, Any]:
        """Envia um XML de NF-e para o Dominio do contador.

        Retorna {batch_id, source}. Envio REAL exige allow_send=True.
        """
        if not content:
            raise ValueError("content vazio")
        if self.is_mock:
            digest = hashlib.sha1(content[:1024]).hexdigest()[:24]
            logger.info(
                "onvio_mock.send_nfe_xml file=%s bytes=%d", filename,
                len(content),
            )
            return {"batch_id": f"mock-{digest}", "source": "onvio_mock"}
        raise NotImplementedError  # Task 6

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        """Status de processamento de um envio (batch)."""
        if self.is_mock:
            return {
                "batch_id": batch_id,
                "stored": True,
                "message": _STORED_MESSAGE,
                "source": "onvio_mock",
            }
        raise NotImplementedError  # Task 6
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_onvio_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/integrations/onvio/ apps/api/tests/test_onvio_client.py
git commit -m "feat(onvio): adapter com mock deterministico (send NF-e, status, activation)"
```

---

### Task 5: OnvioClient — token OAuth com cache 24h + activation

**Files:**
- Modify: `apps/api/app/integrations/onvio/client.py`
- Test: `apps/api/tests/test_onvio_client.py` (append)

**Interfaces:**
- Consumes: esqueleto da Task 4
- Produces: `check_activation()` real; privados `_get_token()`, `_get_activation_key()`, `_api_headers()` usados pela Task 6

- [ ] **Step 1: Write the failing tests** (append)

```python
from httpx import AsyncClient, MockTransport, Request, Response

from app.integrations.onvio.client import OnvioAuthError

CREDS = dict(
    client_id="cid", client_secret="csec", integration_key="ikey"
)


def _real_onvio(handler, **kwargs):
    transport = MockTransport(handler)
    http = AsyncClient(transport=transport)
    return OnvioClient(client=http, **{**CREDS, **kwargs})


def _route(request: Request, *, token_calls: list) -> Response | None:
    """Rotas compartilhadas de auth/activation para os handlers."""
    if request.url.host == "auth.thomsonreuters.com":
        token_calls.append(1)
        body = request.content.decode()
        assert "grant_type=client_credentials" in body
        assert "client_id=cid" in body
        return Response(
            200, json={"access_token": "tok-abc", "expires_in": 86400}
        )
    if request.url.path.endswith("/activation/info"):
        assert request.headers["Authorization"] == "Bearer tok-abc"
        assert request.headers["x-integration-key"] == "ikey"
        return Response(
            200,
            json={
                "accountantOfficeNationalIdentity": "11222333000181",
                "clientNationalIdentity": "99888777000162",
            },
        )
    if request.url.path.endswith("/activation/enable"):
        assert request.headers["x-integration-key"] == "ikey"
        return Response(200, json={"integrationKey": "sess-key-1"})
    return None


@pytest.mark.asyncio
async def test_real_check_activation_e_cache_de_token():
    token_calls: list = []

    def handler(request: Request) -> Response:
        r = _route(request, token_calls=token_calls)
        assert r is not None, f"rota inesperada: {request.url}"
        return r

    c = _real_onvio(handler)
    info1 = await c.check_activation()
    info2 = await c.check_activation()
    assert info1["escritorio_cnpj"] == "11222333000181"
    assert info1["cliente_cnpj"] == "99888777000162"
    assert info1["source"] == "onvio"
    assert info2 == info1
    assert len(token_calls) == 1  # token cachado entre chamadas


@pytest.mark.asyncio
async def test_real_token_rejeitado_vira_auth_error():
    def handler(request: Request) -> Response:
        return Response(401, json={"error": "access_denied"})

    c = _real_onvio(handler)
    with pytest.raises(OnvioAuthError):
        await c.check_activation()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run --extra dev pytest tests/test_onvio_client.py -v -k real`
Expected: FAIL com `NotImplementedError`

- [ ] **Step 3: Write the implementation** — substituir o `NotImplementedError` de `check_activation` e acrescentar os privados:

```python
    # (em check_activation, no lugar do NotImplementedError)
        data = await self._get_json(
            f"{ONVIO_API_BASE}/dominio/integration/v1/activation/info"
        )
        return {
            "escritorio_cnpj": str(
                data.get("accountantOfficeNationalIdentity") or ""
            ),
            "cliente_cnpj": str(data.get("clientNationalIdentity") or ""),
            "source": "onvio",
        }

    # --------------------------- HTTP interno ----------------------------

    async def _get_token(self) -> str:
        # Token dura ~24h; renova com 5 min de folga. Lock evita
        # thundering-herd no /oauth/token (padrao DominioClient).
        async with self._token_lock:
            if self._token and self._token_expires_at - time.time() > 300:
                return self._token
            try:
                r = await self._client.post(
                    ONVIO_AUTH_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "audience": self._audience,
                    },
                )
            except httpx.HTTPError as exc:
                raise OnvioError(f"token Onvio falhou: {exc}") from exc
            if r.status_code in (401, 403):
                raise OnvioAuthError(
                    f"credenciais Onvio rejeitadas ({r.status_code}): "
                    f"{r.text[:200]}"
                )
            if r.status_code != 200:
                raise OnvioError(
                    f"token Onvio {r.status_code}: {r.text[:200]}"
                )
            data = r.json()
            token = data.get("access_token")
            if not token:
                raise OnvioError("resposta /oauth/token sem access_token")
            self._token = str(token)
            self._token_expires_at = time.time() + int(
                data.get("expires_in", 86400)
            )
            return self._token

    async def _api_headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "x-integration-key": self._integration_key,
        }

    async def _get_activation_key(self) -> str:
        # integrationKey de SESSAO devolvida pelo /enable -- usada nos
        # calls de invoice no lugar da key configurada (fluxo do script
        # de referencia; reutilizacao entre envios a confirmar).
        if self._activation_key:
            return self._activation_key
        data = await self._post_json(
            f"{ONVIO_API_BASE}/dominio/integration/v1/activation/enable"
        )
        key = data.get("integrationKey")
        if not key:
            raise OnvioError("resposta /activation/enable sem integrationKey")
        self._activation_key = str(key)
        return self._activation_key

    async def _get_json(self, url: str) -> dict[str, Any]:
        headers = await self._api_headers()
        try:
            r = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise OnvioError(f"transporte Onvio ({url}): {exc}") from exc
        return self._json_or_raise(r, url)

    async def _post_json(self, url: str) -> dict[str, Any]:
        headers = await self._api_headers()
        try:
            r = await self._client.post(url, headers=headers)
        except httpx.HTTPError as exc:
            raise OnvioError(f"transporte Onvio ({url}): {exc}") from exc
        return self._json_or_raise(r, url)

    def _json_or_raise(self, r: httpx.Response, url: str) -> dict[str, Any]:
        if r.status_code in (401, 403):
            # Token pode ter expirado entre cache e uso -- invalida para
            # o proximo call renovar.
            self._token = None
            self._token_expires_at = 0.0
            raise OnvioAuthError(
                f"Onvio {url} status {r.status_code}: {r.text[:200]}"
            )
        if r.status_code >= 300:
            raise OnvioError(
                f"Onvio {url} status {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise OnvioError(
                f"resposta nao-JSON Onvio ({url}): {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise OnvioError(
                f"Onvio {url}: objeto esperado, veio {type(data).__name__}"
            )
        return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_onvio_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/integrations/onvio/client.py apps/api/tests/test_onvio_client.py
git commit -m "feat(onvio): token OAuth com cache 24h + activation info/enable"
```

---

### Task 6: OnvioClient — envio real de NF-e + guard ONVIO_ALLOW_SEND

**Files:**
- Modify: `apps/api/app/integrations/onvio/client.py`
- Test: `apps/api/tests/test_onvio_client.py` (append)

**Interfaces:**
- Consumes: `_api_headers()`, `_get_activation_key()`, `_json_or_raise()` da Task 5
- Produces: `send_nfe_xml`/`get_batch_status` reais; `OnvioSendBlockedError` quando `allow_send=False`

- [ ] **Step 1: Write the failing tests** (append)

```python
@pytest.mark.asyncio
async def test_real_send_bloqueado_sem_allow_send():
    def handler(request: Request) -> Response:
        raise AssertionError("nenhuma chamada HTTP deveria acontecer")

    c = _real_onvio(handler, allow_send=False)
    with pytest.raises(OnvioSendBlockedError):
        await c.send_nfe_xml(filename="nf.xml", content=XML)


@pytest.mark.asyncio
async def test_real_send_e_status():
    token_calls: list = []

    def handler(request: Request) -> Response:
        shared = _route(request, token_calls=token_calls)
        if shared is not None:
            return shared
        if request.url.path == "/dominio/invoice/v3/batches":
            # invoice usa a integrationKey de SESSAO do /enable
            assert request.headers["x-integration-key"] == "sess-key-1"
            assert request.method == "POST"
            assert b"NFe123" in request.content
            return Response(200, json={"id": "batch-77"})
        if request.url.path == "/dominio/invoice/v3/batches/batch-77":
            return Response(
                200,
                json={
                    "filesExpanded": [
                        {"apiStatus": {"message": "Arquivo armazenado na API"}}
                    ]
                },
            )
        raise AssertionError(f"rota inesperada: {request.url}")

    c = _real_onvio(handler, allow_send=True)
    sent = await c.send_nfe_xml(filename="nf.xml", content=XML)
    assert sent == {"batch_id": "batch-77", "source": "onvio"}
    st = await c.get_batch_status("batch-77")
    assert st["stored"] is True
    assert st["message"] == "Arquivo armazenado na API"
    assert st["source"] == "onvio"


@pytest.mark.asyncio
async def test_real_status_nao_armazenado():
    token_calls: list = []

    def handler(request: Request) -> Response:
        shared = _route(request, token_calls=token_calls)
        if shared is not None:
            return shared
        return Response(
            200,
            json={
                "filesExpanded": [
                    {"apiStatus": {"message": "Arquivo com schema invalido"}}
                ]
            },
        )

    c = _real_onvio(handler, allow_send=True)
    st = await c.get_batch_status("batch-99")
    assert st["stored"] is False
    assert st["message"] == "Arquivo com schema invalido"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run --extra dev pytest tests/test_onvio_client.py -v -k "send or status"`
Expected: FAIL com `NotImplementedError`

- [ ] **Step 3: Write the implementation** — substituir os `NotImplementedError` de `send_nfe_xml` e `get_batch_status`:

```python
    # (em send_nfe_xml, no lugar do NotImplementedError)
        if not self._allow_send:
            raise OnvioSendBlockedError(
                "envio real de NF-e ao Onvio bloqueado: e escrita no "
                "Dominio de PRODUCAO do escritorio contabil. Se "
                "intencional, setar ONVIO_ALLOW_SEND=true."
            )
        activation_key = await self._get_activation_key()
        token = await self._get_token()
        files = {
            "file[]": (filename, content, "application/xml"),
            "query": (None, '{"boxe/File": false}', "application/json"),
        }
        try:
            r = await self._client.post(
                f"{ONVIO_API_BASE}/dominio/invoice/v3/batches",
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-integration-key": activation_key,
                },
                files=files,
            )
        except httpx.HTTPError as exc:
            raise OnvioError(f"envio NF-e Onvio falhou: {exc}") from exc
        data = self._json_or_raise(r, "/dominio/invoice/v3/batches")
        batch_id = data.get("id")
        if not batch_id:
            raise OnvioError(f"resposta de envio sem id: {data}")
        return {"batch_id": str(batch_id), "source": "onvio"}

    # (em get_batch_status, no lugar do NotImplementedError)
        activation_key = await self._get_activation_key()
        token = await self._get_token()
        url = f"{ONVIO_API_BASE}/dominio/invoice/v3/batches/{batch_id}"
        try:
            r = await self._client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-integration-key": activation_key,
                },
            )
        except httpx.HTTPError as exc:
            raise OnvioError(f"status de batch Onvio falhou: {exc}") from exc
        data = self._json_or_raise(r, url)
        expanded = data.get("filesExpanded") or []
        message = ""
        if expanded and isinstance(expanded[0], dict):
            message = str(
                (expanded[0].get("apiStatus") or {}).get("message") or ""
            )
        return {
            "batch_id": batch_id,
            "stored": message == _STORED_MESSAGE,
            "message": message,
            "source": "onvio",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run --extra dev pytest tests/test_onvio_client.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Lint e commit**

```bash
cd apps/api && uv run ruff check app/integrations/onvio tests/test_onvio_client.py && uv run ruff format app/integrations/onvio tests/test_onvio_client.py
git add apps/api/app/integrations/onvio/ apps/api/tests/test_onvio_client.py
git commit -m "feat(onvio): envio real de NF-e com guard ONVIO_ALLOW_SEND"
```

---

### Task 7: Documentação + suite completa

**Files:**
- Modify: `docs/integrations.md` (linhas 9-15: status na tabela; nova seção ao final)

**Interfaces:**
- Consumes: tudo acima
- Produces: doc de referência para as squads de mão de obra e fiscal

- [ ] **Step 1: Atualizar a tabela de integrações** — nas linhas do `onvio` e `tangerino` (ver `grep -n "onvio\|tangerino" docs/integrations.md`), marcar status "adapter pronto (mock por default)" seguindo o formato das linhas vizinhas já implementadas (ex.: onsafety).

- [ ] **Step 2: Acrescentar seção ao final de `docs/integrations.md`:**

```markdown
## Tangerino (ponto eletrônico — Sólides)

Adapter em `app/integrations/tangerino/`. Sem `TANGERINO_API_KEY`, mock
determinístico (3 funcionários, 2 obras). Endpoints validados contra o
spec público `https://employer.tangerino.com.br/v2/api-docs` (2026-08-04):
funcionários (`/employee/find-all`), batidas
(`/external/api/v1/payssego/punches/{id}`), locais de trabalho
(`/workplace/find-all`). Auth: api key crua no header `Authorization`.

**Limitações descobertas no spec (impactam a apropriação de mão de obra):**

- **Não há geolocalização nas batidas** (`PunchSimpleDTO` só tem
  timestamps) nem em nenhum modelo do spec público. O vínculo
  funcionário→obra confiável é o **workplace** (local de trabalho)
  associado ao funcionário. A hipótese "localização da batida" do
  documento de mão de obra precisa ser confirmada com o suporte Sólides
  — pode existir em outra superfície de API.
- Não há endpoint de afastamentos no spec público.
- Unidade dos timestamps (epoch ms assumido) e formato de
  `startDate`/`endDate` a confirmar com credencial real.

| Env | Uso |
|---|---|
| `TANGERINO_API_KEY` | api key (vazio = mock) |
| `TANGERINO_BASE_URL` | default `https://employer.tangerino.com.br` |

## Onvio (Domínio/Thomson Reuters — NF-e para o contador)

Adapter em `app/integrations/onvio/`. Sem qualquer uma das 3 credenciais,
mock determinístico. Fluxo: token OAuth2 (`auth.thomsonreuters.com`,
cache 24h em memória) → activation (`/dominio/integration/v1/activation/*`)
→ envio (`POST /dominio/invoice/v3/batches`, multipart) → status
(`GET /batches/{id}`; sucesso = mensagem "Arquivo armazenado na API").

**Guard-rail:** envio real é escrita no Domínio de PRODUÇÃO do escritório
contábil (sem sandbox conhecido). `ONVIO_ALLOW_SEND=false` (default)
bloqueia `send_nfe_xml` real com `OnvioSendBlockedError`; mock não é
afetado. Coexiste com `app/integrations/dominio` (Central do
Desenvolvedor) — qual superfície o módulo fiscal usa é decisão de
serviço, não do adapter.

| Env | Uso |
|---|---|
| `ONVIO_CLIENT_ID` / `ONVIO_CLIENT_SECRET` | credenciais OAuth Thomson Reuters |
| `ONVIO_INTEGRATION_KEY` | chave de integração do vínculo contador↔cliente |
| `ONVIO_AUDIENCE` | default `409f91f6-dc17-44c8-a5d8-e0a1bafd8b67` |
| `ONVIO_ALLOW_SEND` | `false` (default) bloqueia envio real |
```

- [ ] **Step 3: Rodar a suite completa + lint**

Run: `cd apps/api && uv run --extra dev pytest && uv run ruff check app tests && uv run ruff format --check app tests`
Expected: todos os testes verdes (565 pré-existentes + novos), ruff limpo

- [ ] **Step 4: Commit**

```bash
git add docs/integrations.md
git commit -m "docs(integracoes): Tangerino e Onvio -- endpoints, envs, guard e limitacao de geolocalizacao"
```
