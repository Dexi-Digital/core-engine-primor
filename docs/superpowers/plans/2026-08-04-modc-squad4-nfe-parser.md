# Squad 4 — NF-e detalhada (impostos, itens, obra) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Estender o módulo `fiscal` existente para extrair da NF-e/NFC-e os dados que a migração 90→TOTVS precisa — validação do DV da chave de acesso, UF, impostos (ICMS/IPI/PIS/COFINS), itens (NCM/CFOP/quantidades/valores) — persistir tudo, permitir vincular nota a obra, filtrar por período/obra/valor, e expor na UI.

**Architecture:** O repo **já tem** um parser fiscal multi-tipo (`apps/api/app/modules/fiscal/parser.py`, função `parse_xml`) que extrai metadados mínimos (chave, partes, valor, data) e um model `DocumentoFiscal` com dedup por chave/hash e upload via `POST /api/v1/fiscal/documentos`. Este plano NÃO cria um parser novo nem tabela `fiscal_notas_fiscais` — ele adiciona um segundo passe de extração (`parse_nfe_detalhes`) só para NF-e/NFC-e, colunas novas em `fiscal_documentos`, a tabela filha `fiscal_documento_itens`, e um endpoint de reprocessamento para enriquecer documentos importados antes desta feature (o XML bruto fica no storage exatamente para isso — ver docstring do parser).

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic + Pydantic v2 + defusedxml (já é dependência — NÃO adicionar lxml) + pytest-asyncio + Next.js App Router (server components).

## Global Constraints

- Rodar comandos Python a partir de `apps/api/`; testes: `uv run --extra dev pytest <path> -v`; lint: `uv run --extra dev ruff check .` (deve passar antes de cada commit).
- XML sempre via `defusedxml` (proteção XXE) — nunca `xml.etree` direto, nunca dependência nova.
- Toda mutação grava `AuditLog` (padrão `_record_audit` já existente em `app/modules/fiscal/service.py`; AGENTS.md exige).
- Metadados extraídos do XML **não são editáveis** pela UI (auditoria fiscal) — PATCH só muda `observacoes`, `status_envio` e `obra_id`.
- Valores monetários `Decimal`/`Numeric(20, 2)`; quantidades `Numeric(20, 4)`; valor unitário `Numeric(20, 10)` (precisão do leiaute NF-e 4.00).
- Migration nova: `down_revision` deve apontar para o head vigente. No momento da escrita deste plano o head é `b8c9d0e1f2a3` — **confirme com `uv run --extra dev alembic heads` antes de criar** (squads paralelas também criam migrations).
- Validação de DV da chave é **informativa** (flag persistida), nunca rejeita o upload — a fixture legada `NFE_44_XML` tem DV inválido de propósito e os testes existentes (565) não podem quebrar.
- Docstrings/comentários em pt-BR no estilo do módulo (explicam decisão, não o óbvio).
- Commits: mensagens `feat(fiscal): ...` / `test(fiscal): ...` em pt-BR sem acento, como o histórico do repo.

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `apps/api/app/modules/fiscal/parser.py` (modificar) | + `validar_chave_acesso`, `NfeItem`, `NfeDetalhes`, `parse_nfe_detalhes` |
| `apps/api/app/modules/fiscal/models.py` (modificar) | + colunas em `DocumentoFiscal`; + model `DocumentoFiscalItem` |
| `apps/api/alembic/versions/c7d8e9f0a1b2_fiscal_nfe_detalhes.py` (criar) | migration das colunas + tabela de itens |
| `apps/api/app/modules/fiscal/service.py` (modificar) | enriquecer `import_xml`; + `list_itens`, `reprocessar_documento`; filtros novos em `list_documentos`; `obra_id` em `update_documento` |
| `apps/api/app/modules/fiscal/schemas.py` (modificar) | campos novos em `DocumentoFiscalRead`/`Update`; + `DocumentoFiscalItemRead`, `DocumentoFiscalDetail` |
| `apps/api/app/modules/fiscal/router.py` (modificar) | detail com itens; filtros; `POST /documentos/{id}/reprocessar` |
| `apps/api/tests/fixtures/fiscal/samples.py` (modificar) | + `NFE_DETALHADA_XML` |
| `apps/api/tests/test_fiscal_parser.py` (modificar) | testes de DV + detalhes |
| `apps/api/tests/test_fiscal_router.py` (modificar) | testes E2E de upload enriquecido, filtros, obra, reprocessar |
| `apps/web/src/app/(dashboard)/fiscal/documentos/page.tsx` (modificar) | filtros período/obra + colunas UF/Obra + link p/ detalhe |
| `apps/web/src/app/(dashboard)/fiscal/documentos/[id]/page.tsx` (criar) | página de detalhe: impostos, itens, vincular obra |

---

### Task 1: Validação de DV da chave de acesso

**Files:**
- Modify: `apps/api/app/modules/fiscal/parser.py`
- Test: `apps/api/tests/test_fiscal_parser.py`

**Interfaces:**
- Consumes: nada novo (usa `re` já importado no módulo).
- Produces: `validar_chave_acesso(chave: str) -> bool` — usada pela Task 2 (`parse_nfe_detalhes`) e indiretamente persistida como `chave_dv_valida` na Task 4.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `apps/api/tests/test_fiscal_parser.py`:

```python
# ---------------------------------------------------------------------------
# validar_chave_acesso (DV modulo-11 do leiaute NF-e 4.00)
# ---------------------------------------------------------------------------


def test_validar_chave_acesso_dv_correto():
    # DV 8 calculado pelo algoritmo oficial (pesos 2..9 da direita p/ esquerda)
    assert validar_chave_acesso("35240414200166000187550010000543211000000008") is True


def test_validar_chave_acesso_dv_errado():
    # Mesma chave com DV trocado -> invalida
    assert validar_chave_acesso("35240414200166000187550010000543211000000001") is False


def test_validar_chave_acesso_fixture_legada_tem_dv_invalido():
    """A chave da NFE_44_XML e sintetica com DV errado -- documenta que
    a validacao e informativa e NAO pode rejeitar upload (Global
    Constraint: 565 testes legados nao quebram)."""
    assert validar_chave_acesso("35240414200166000187550010000123451000000001") is False


def test_validar_chave_acesso_formato_invalido():
    assert validar_chave_acesso("") is False
    assert validar_chave_acesso("123") is False
    assert validar_chave_acesso("A" * 44) is False
```

E no bloco de imports do mesmo arquivo, trocar a linha
`from app.modules.fiscal.parser import FiscalParseError, parse_xml` por:

```python
from app.modules.fiscal.parser import (
    FiscalParseError,
    parse_xml,
    validar_chave_acesso,
)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_parser.py -v`
Expected: FAIL com `ImportError: cannot import name 'validar_chave_acesso'`

- [ ] **Step 3: Implementar**

Em `apps/api/app/modules/fiscal/parser.py`, adicionar após a função `parse_xml` (final do arquivo):

```python
def validar_chave_acesso(chave: str) -> bool:
    """Valida o digito verificador (modulo-11) da chave de acesso NF-e.

    Informativa: chave com DV errado indica XML adulterado ou gerado a
    mao, mas NAO bloqueia importacao -- o dado ainda tem valor contabil
    e a rejeicao seria falso-positivo em XMLs de homologacao/sinteticos.
    """
    if not re.fullmatch(r"\d{44}", chave or ""):
        return False
    pesos = (2, 3, 4, 5, 6, 7, 8, 9)
    soma = sum(
        int(digito) * pesos[i % 8]
        for i, digito in enumerate(reversed(chave[:43]))
    )
    resto = soma % 11
    dv = 0 if resto in (0, 1) else 11 - resto
    return dv == int(chave[43])
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_parser.py -v`
Expected: PASS (todos, incluindo os legados)

- [ ] **Step 5: Lint + commit**

```bash
cd apps/api && uv run --extra dev ruff check .
git add apps/api/app/modules/fiscal/parser.py apps/api/tests/test_fiscal_parser.py
git commit -m "feat(fiscal): validacao modulo-11 do DV da chave de acesso NF-e"
```

---

### Task 2: `parse_nfe_detalhes` — UF, impostos e itens

**Files:**
- Modify: `apps/api/app/modules/fiscal/parser.py`
- Modify: `apps/api/tests/fixtures/fiscal/samples.py`
- Test: `apps/api/tests/test_fiscal_parser.py`

**Interfaces:**
- Consumes: `validar_chave_acesso(chave: str) -> bool` (Task 1); helpers privados existentes do módulo: `_find_first`, `_text`, `_decimal`, `_local`, `_detect_tipo`, `FiscalParseError`.
- Produces: `parse_nfe_detalhes(content: bytes) -> NfeDetalhes | None` (None para tipos ≠ nfe/nfce); dataclasses `NfeItem` (ordem, codigo, descricao, ncm, cfop, unidade, quantidade, valor_unitario, valor_total) e `NfeDetalhes` (uf, chave_dv_valida, valor_icms, valor_ipi, valor_pis, valor_cofins, itens) — consumidas pelas Tasks 4 e 6. Fixture `NFE_DETALHADA_XML` — consumida pelas Tasks 4, 5, 6.

- [ ] **Step 1: Criar a fixture `NFE_DETALHADA_XML`**

Adicionar em `apps/api/tests/fixtures/fiscal/samples.py`, logo após `NFE_44_XML` (antes de `NFCE_65_XML`):

```python
# NF-e com itens + totais de impostos + endereco do emitente. Chave com
# DV valido (8) -- calculado pelo modulo-11 oficial -- para exercitar
# `chave_dv_valida=True` (a NFE_44_XML acima tem DV proposital/invalido).
NFE_DETALHADA_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35240414200166000187550010000543211000000008" versao="4.00">
      <ide>
        <cUF>35</cUF>
        <natOp>VENDA</natOp>
        <mod>55</mod>
        <serie>1</serie>
        <nNF>54321</nNF>
        <dhEmi>2024-04-22T09:15:00-03:00</dhEmi>
        <tpNF>1</tpNF>
      </ide>
      <emit>
        <CNPJ>14200166000187</CNPJ>
        <xNome>Construtora Primor LTDA</xNome>
        <enderEmit>
          <xMun>Sao Paulo</xMun>
          <UF>SP</UF>
        </enderEmit>
      </emit>
      <dest>
        <CNPJ>33000167000101</CNPJ>
        <xNome>Petrobras Distribuidora</xNome>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>CIM-CP2</cProd>
          <xProd>Cimento CP-II 50kg</xProd>
          <NCM>25232910</NCM>
          <CFOP>5102</CFOP>
          <uCom>SC</uCom>
          <qCom>100.0000</qCom>
          <vUnCom>200.0000000000</vUnCom>
          <vProd>20000.00</vProd>
        </prod>
      </det>
      <det nItem="2">
        <prod>
          <cProd>ACO-CA50</cProd>
          <xProd>Vergalhao CA-50 12mm</xProd>
          <NCM>72142000</NCM>
          <CFOP>5102</CFOP>
          <uCom>BR</uCom>
          <qCom>10.0000</qCom>
          <vUnCom>500.0000000000</vUnCom>
          <vProd>5000.00</vProd>
        </prod>
      </det>
      <total>
        <ICMSTot>
          <vICMS>3000.00</vICMS>
          <vIPI>250.00</vIPI>
          <vPIS>165.00</vPIS>
          <vCOFINS>760.00</vCOFINS>
          <vNF>25000.00</vNF>
        </ICMSTot>
      </total>
    </infNFe>
  </NFe>
</nfeProc>
"""
```

- [ ] **Step 2: Escrever os testes que falham**

Adicionar ao final de `apps/api/tests/test_fiscal_parser.py`:

```python
# ---------------------------------------------------------------------------
# parse_nfe_detalhes (UF + impostos + itens -- 2o passe, so NF-e/NFC-e)
# ---------------------------------------------------------------------------


def test_parse_nfe_detalhes_extrai_impostos_uf_e_itens():
    det = parse_nfe_detalhes(NFE_DETALHADA_XML)
    assert det is not None
    assert det.uf == "SP"
    assert det.chave_dv_valida is True
    assert det.valor_icms == Decimal("3000.00")
    assert det.valor_ipi == Decimal("250.00")
    assert det.valor_pis == Decimal("165.00")
    assert det.valor_cofins == Decimal("760.00")

    assert len(det.itens) == 2
    item1 = det.itens[0]
    assert item1.ordem == 1
    assert item1.codigo == "CIM-CP2"
    assert item1.descricao == "Cimento CP-II 50kg"
    assert item1.ncm == "25232910"
    assert item1.cfop == "5102"
    assert item1.unidade == "SC"
    assert item1.quantidade == Decimal("100.0000")
    assert item1.valor_unitario == Decimal("200.0000000000")
    assert item1.valor_total == Decimal("20000.00")
    assert det.itens[1].ordem == 2
    assert det.itens[1].codigo == "ACO-CA50"


def test_parse_nfe_detalhes_sem_ender_emit_cai_no_cuf_da_chave():
    """NFE_44_XML nao tem <enderEmit>; a UF vem do codigo cUF (35=SP)
    embutido na chave de acesso. E o DV invalido da fixture legada vira
    flag False -- nao erro."""
    det = parse_nfe_detalhes(NFE_44_XML)
    assert det is not None
    assert det.uf == "SP"
    assert det.chave_dv_valida is False
    assert det.itens == []  # fixture minima nao tem <det>
    assert det.valor_icms is None  # ICMSTot minimo so tem vNF


def test_parse_nfe_detalhes_tipo_nao_suportado_retorna_none():
    assert parse_nfe_detalhes(CTE_XML) is None
    assert parse_nfe_detalhes(NFSE_ABRASF_XML) is None
    assert parse_nfe_detalhes(BAIXA_XML) is None


def test_parse_nfe_detalhes_nfce_tambem_suportada():
    det = parse_nfe_detalhes(NFCE_65_XML)
    assert det is not None


def test_parse_nfe_detalhes_xml_malformado_levanta_erro():
    with pytest.raises(FiscalParseError):
        parse_nfe_detalhes(b"<NFe><infNFe")
```

Atualizar o import do parser no topo do arquivo para:

```python
from app.modules.fiscal.parser import (
    FiscalParseError,
    parse_nfe_detalhes,
    parse_xml,
    validar_chave_acesso,
)
```

E acrescentar `NFE_DETALHADA_XML` ao import de `tests.fixtures.fiscal.samples`.

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_parser.py -v`
Expected: FAIL com `ImportError: cannot import name 'parse_nfe_detalhes'`

- [ ] **Step 4: Implementar**

Em `apps/api/app/modules/fiscal/parser.py`:

(a) adicionar `field` ao import de dataclasses (linha `from dataclasses import dataclass` vira):

```python
from dataclasses import dataclass, field
```

(b) adicionar ao final do arquivo:

```python
# Codigo IBGE (cUF, posicoes 0-1 da chave de acesso) -> sigla. Fallback
# para quando o XML nao traz <enderEmit><UF> (emissores minimos).
_CUF_UF = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA",
    "16": "AP", "17": "TO", "21": "MA", "22": "PI", "23": "CE",
    "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE",
    "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
    "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT",
    "52": "GO", "53": "DF",
}


@dataclass
class NfeItem:
    """Um <det> da NF-e -- o minimo para apropriacao de custo por obra."""

    ordem: int
    codigo: str | None
    descricao: str | None
    ncm: str | None
    cfop: str | None
    unidade: str | None
    quantidade: Decimal | None
    valor_unitario: Decimal | None
    valor_total: Decimal | None


@dataclass
class NfeDetalhes:
    """2o passe de extracao, exclusivo NF-e/NFC-e (a migracao 90->TOTVS
    precisa de impostos + itens; os outros tipos seguem so com o
    ParsedDocumento minimo)."""

    uf: str | None
    chave_dv_valida: bool | None
    valor_icms: Decimal | None
    valor_ipi: Decimal | None
    valor_pis: Decimal | None
    valor_cofins: Decimal | None
    itens: list[NfeItem] = field(default_factory=list)


def parse_nfe_detalhes(content: bytes) -> NfeDetalhes | None:
    """Extrai UF, totais de impostos e itens de uma NF-e/NFC-e.

    Retorna None para tipos que nao sejam nfe/nfce (deteccao identica
    ao `parse_xml`). Levanta `FiscalParseError` nos mesmos casos que
    `parse_xml` (XML malformado/vazio/desconhecido).
    """
    if not content or not content.strip():
        raise FiscalParseError("XML vazio")
    try:
        root = ET.fromstring(content)
    except DefusedXmlException as exc:
        raise FiscalParseError(
            f"XML rejeitado por seguranca (entidades externas): {exc}"
        ) from exc
    except ET.ParseError as exc:
        raise FiscalParseError(f"XML malformado: {exc}") from exc

    if _detect_tipo(root) not in {"nfe", "nfce"}:
        return None

    inf = _find_first(root, "infNFe")
    if inf is None:
        raise FiscalParseError("NF-e/NFC-e sem <infNFe>")

    chave = inf.get("Id", "")
    if chave.startswith("NFe"):
        chave = chave[3:]
    dv_valida: bool | None = None
    uf: str | None = None
    if re.fullmatch(r"\d{44}", chave):
        dv_valida = validar_chave_acesso(chave)
        uf = _CUF_UF.get(chave[:2])

    emit = _find_first(inf, "emit")
    if emit is not None:
        ender = _find_first(emit, "enderEmit")
        if ender is not None:
            uf = _text(_find_first(ender, "UF")) or uf

    icms_tot = _find_first(inf, "ICMSTot")

    def _total(tag: str) -> Decimal | None:
        if icms_tot is None:
            return None
        return _decimal(_text(_find_first(icms_tot, tag)))

    itens: list[NfeItem] = []
    dets = [el for el in inf.iter() if _local(el.tag) == "det"]
    for i, det in enumerate(dets, start=1):
        prod = _find_first(det, "prod")
        if prod is None:
            continue
        try:
            ordem = int(det.get("nItem", i))
        except (TypeError, ValueError):
            ordem = i
        itens.append(
            NfeItem(
                ordem=ordem,
                codigo=_text(_find_first(prod, "cProd")),
                descricao=_text(_find_first(prod, "xProd")),
                ncm=_text(_find_first(prod, "NCM")),
                cfop=_text(_find_first(prod, "CFOP")),
                unidade=_text(_find_first(prod, "uCom")),
                quantidade=_decimal(_text(_find_first(prod, "qCom"))),
                valor_unitario=_decimal(_text(_find_first(prod, "vUnCom"))),
                valor_total=_decimal(_text(_find_first(prod, "vProd"))),
            )
        )

    return NfeDetalhes(
        uf=uf,
        chave_dv_valida=dv_valida,
        valor_icms=_total("vICMS"),
        valor_ipi=_total("vIPI"),
        valor_pis=_total("vPIS"),
        valor_cofins=_total("vCOFINS"),
        itens=itens,
    )
```

- [ ] **Step 5: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_parser.py -v`
Expected: PASS

- [ ] **Step 6: Lint + commit**

```bash
cd apps/api && uv run --extra dev ruff check .
git add apps/api/app/modules/fiscal/parser.py apps/api/tests/test_fiscal_parser.py apps/api/tests/fixtures/fiscal/samples.py
git commit -m "feat(fiscal): parse_nfe_detalhes -- UF, impostos e itens da NF-e"
```

---

### Task 3: Models + migration (colunas novas + `fiscal_documento_itens`)

**Files:**
- Modify: `apps/api/app/modules/fiscal/models.py`
- Create: `apps/api/alembic/versions/c7d8e9f0a1b2_fiscal_nfe_detalhes.py`
- Test: `apps/api/tests/test_fiscal_router.py` (teste de persistência via `db_session`)

**Interfaces:**
- Consumes: `Base` de `app.core.db`; tabela `obras_obra` (FK).
- Produces: colunas novas em `DocumentoFiscal`: `uf: str | None`, `chave_dv_valida: bool | None`, `valor_icms/valor_ipi/valor_pis/valor_cofins: Decimal | None`, `obra_id: int | None`; model `DocumentoFiscalItem` (tabela `fiscal_documento_itens`, campos: `id`, `documento_id`, `ordem`, `codigo`, `descricao`, `ncm`, `cfop`, `unidade`, `quantidade`, `valor_unitario`, `valor_total`) — consumidos pelas Tasks 4, 5, 6.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `apps/api/tests/test_fiscal_router.py`:

```python
# ---------------------------------------------------------------------------
# Task 3 -- persistencia de detalhes NF-e (colunas novas + tabela de itens)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modelo_persiste_detalhes_e_itens(db_session: AsyncSession):
    doc = DocumentoFiscal(
        tipo="nfe",
        chave_acesso="35240414200166000187550010000543211000000008",
        xml_path="fiscal/1/teste.xml",
        xml_hash="deadbeef" * 8,
        status_envio="pendente",
        retry_count=0,
        uf="SP",
        chave_dv_valida=True,
        valor_icms=Decimal("3000.00"),
        valor_ipi=Decimal("250.00"),
        valor_pis=Decimal("165.00"),
        valor_cofins=Decimal("760.00"),
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        DocumentoFiscalItem(
            documento_id=doc.id,
            ordem=1,
            codigo="CIM-CP2",
            descricao="Cimento CP-II 50kg",
            ncm="25232910",
            cfop="5102",
            unidade="SC",
            quantidade=Decimal("100.0000"),
            valor_unitario=Decimal("200.00"),
            valor_total=Decimal("20000.00"),
        )
    )
    await db_session.commit()

    row = await db_session.scalar(
        select(DocumentoFiscalItem).where(
            DocumentoFiscalItem.documento_id == doc.id
        )
    )
    assert row is not None
    assert row.ncm == "25232910"
    assert doc.uf == "SP"
    assert doc.chave_dv_valida is True
    assert doc.obra_id is None
```

Ajustar imports no topo do arquivo: acrescentar `Decimal`
(`from decimal import Decimal`) e trocar
`from app.modules.fiscal.models import DocumentoFiscal` por:

```python
from app.modules.fiscal.models import DocumentoFiscal, DocumentoFiscalItem
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_router.py::test_modelo_persiste_detalhes_e_itens -v`
Expected: FAIL com `ImportError: cannot import name 'DocumentoFiscalItem'`

- [ ] **Step 3: Implementar os models**

Em `apps/api/app/modules/fiscal/models.py`:

(a) acrescentar `Boolean` e `ForeignKey` ao import de `sqlalchemy` (o bloco vira):

```python
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
```

(b) dentro de `DocumentoFiscal`, logo após o campo `data_emissao`, adicionar:

```python
    # --- 2o passe NF-e/NFC-e (parse_nfe_detalhes). Nulos para os demais
    # tipos e para documentos importados antes da feature (use o
    # endpoint /reprocessar para preencher retroativamente).
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    chave_dv_valida: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    valor_icms: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    valor_ipi: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    valor_pis: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    valor_cofins: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )
    # Vinculo manual com obra (rastreabilidade de custo). SET NULL: a
    # exclusao de uma obra nao pode apagar documento fiscal (auditoria).
    obra_id: Mapped[int | None] = mapped_column(
        ForeignKey("obras_obra.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
```

(c) ao final do arquivo, adicionar a classe:

```python
class DocumentoFiscalItem(Base):
    """Um item (<det>) de NF-e/NFC-e. Filha de DocumentoFiscal.

    CASCADE no delete: item nao existe sem a nota (o delete da nota ja
    audita o snapshot; itens nao precisam de trilha propria).
    """

    __tablename__ = "fiscal_documento_itens"

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("fiscal_documentos.id", ondelete="CASCADE"), index=True
    )
    ordem: Mapped[int] = mapped_column(Integer)
    codigo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    descricao: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ncm: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cfop: Mapped[str | None] = mapped_column(String(8), nullable=True)
    unidade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantidade: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 4), nullable=True
    )
    valor_unitario: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    valor_total: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 2), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "documento_id", "ordem", name="uq_fiscal_item_documento_ordem"
        ),
    )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_router.py -v`
Expected: PASS (o schema de teste é criado a partir dos models; a migration é para os bancos reais)

- [ ] **Step 5: Criar a migration**

Antes: `cd apps/api && uv run --extra dev alembic heads` — se o head NÃO for `b8c9d0e1f2a3`, use o head real em `down_revision`.

Criar `apps/api/alembic/versions/c7d8e9f0a1b2_fiscal_nfe_detalhes.py`:

```python
"""fiscal_nfe_detalhes

2o passe de extracao da NF-e (Squad 4 / demanda #8 -- migracao
90->TOTVS): UF, flag de DV da chave, totais de impostos e vinculo com
obra em `fiscal_documentos`; nova tabela filha `fiscal_documento_itens`
com os <det> (NCM/CFOP/quantidades/valores).

Revision ID: c7d8e9f0a1b2
Revises: b8c9d0e1f2a3
Create Date: 2026-08-04
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fiscal_documentos", sa.Column("uf", sa.String(2), nullable=True)
    )
    op.create_index(
        "ix_fiscal_documentos_uf", "fiscal_documentos", ["uf"]
    )
    op.add_column(
        "fiscal_documentos",
        sa.Column("chave_dv_valida", sa.Boolean(), nullable=True),
    )
    for col in ("valor_icms", "valor_ipi", "valor_pis", "valor_cofins"):
        op.add_column(
            "fiscal_documentos",
            sa.Column(col, sa.Numeric(20, 2), nullable=True),
        )
    op.add_column(
        "fiscal_documentos",
        sa.Column("obra_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_fiscal_documentos_obra_id", "fiscal_documentos", ["obra_id"]
    )
    op.create_foreign_key(
        "fk_fiscal_documentos_obra_id",
        "fiscal_documentos",
        "obras_obra",
        ["obra_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "fiscal_documento_itens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "documento_id",
            sa.Integer(),
            sa.ForeignKey("fiscal_documentos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(64), nullable=True),
        sa.Column("descricao", sa.String(512), nullable=True),
        sa.Column("ncm", sa.String(16), nullable=True),
        sa.Column("cfop", sa.String(8), nullable=True),
        sa.Column("unidade", sa.String(16), nullable=True),
        sa.Column("quantidade", sa.Numeric(20, 4), nullable=True),
        sa.Column("valor_unitario", sa.Numeric(20, 10), nullable=True),
        sa.Column("valor_total", sa.Numeric(20, 2), nullable=True),
        sa.UniqueConstraint(
            "documento_id", "ordem", name="uq_fiscal_item_documento_ordem"
        ),
    )
    op.create_index(
        "ix_fiscal_documento_itens_documento_id",
        "fiscal_documento_itens",
        ["documento_id"],
    )


def downgrade() -> None:
    op.drop_table("fiscal_documento_itens")
    op.drop_constraint(
        "fk_fiscal_documentos_obra_id", "fiscal_documentos", type_="foreignkey"
    )
    op.drop_index("ix_fiscal_documentos_obra_id", table_name="fiscal_documentos")
    op.drop_column("fiscal_documentos", "obra_id")
    for col in ("valor_cofins", "valor_pis", "valor_ipi", "valor_icms"):
        op.drop_column("fiscal_documentos", col)
    op.drop_column("fiscal_documentos", "chave_dv_valida")
    op.drop_index("ix_fiscal_documentos_uf", table_name="fiscal_documentos")
    op.drop_column("fiscal_documentos", "uf")
```

- [ ] **Step 6: Validar a suíte inteira + lint**

Run: `cd apps/api && uv run --extra dev pytest -x -q && uv run --extra dev ruff check .`
Expected: PASS (nenhum teste legado quebra)

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/modules/fiscal/models.py apps/api/alembic/versions/c7d8e9f0a1b2_fiscal_nfe_detalhes.py apps/api/tests/test_fiscal_router.py
git commit -m "feat(fiscal): colunas de detalhes NF-e + tabela fiscal_documento_itens"
```

---

### Task 4: Upload enriquecido + detalhe com itens

**Files:**
- Modify: `apps/api/app/modules/fiscal/service.py`
- Modify: `apps/api/app/modules/fiscal/schemas.py`
- Modify: `apps/api/app/modules/fiscal/router.py`
- Test: `apps/api/tests/test_fiscal_router.py`

**Interfaces:**
- Consumes: `parse_nfe_detalhes` (Task 2), `DocumentoFiscalItem` e colunas novas (Task 3).
- Produces: `list_itens(db: AsyncSession, documento_id: int) -> Sequence[DocumentoFiscalItem]` no service; schemas `DocumentoFiscalItemRead` e `DocumentoFiscalDetail` (herda `DocumentoFiscalRead` + `itens: list[DocumentoFiscalItemRead]`); `GET /api/v1/fiscal/documentos/{id}` passa a responder `DocumentoFiscalDetail`. Consumidos pelas Tasks 6 e 7.

- [ ] **Step 1: Escrever o teste E2E que falha**

Adicionar ao final de `apps/api/tests/test_fiscal_router.py` (o import de `NFE_DETALHADA_XML` entra no bloco `from tests.fixtures.fiscal.samples import ...`):

```python
@pytest.mark.asyncio
async def test_upload_nfe_detalhada_persiste_impostos_e_itens(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_DETALHADA_XML, "nfe-detalhada.xml"),
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["uf"] == "SP"
    assert data["chave_dv_valida"] is True
    assert data["valor_icms"] == "3000.00"
    assert data["valor_cofins"] == "760.00"

    detail = await api_client.get(
        f"/api/v1/fiscal/documentos/{data['id']}", headers=auth_headers
    )
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["itens"]) == 2
    assert body["itens"][0]["ncm"] == "25232910"
    assert body["itens"][0]["cfop"] == "5102"
    assert body["itens"][1]["codigo"] == "ACO-CA50"


@pytest.mark.asyncio
async def test_upload_cte_nao_gera_itens_nem_detalhes(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    """Tipos != nfe/nfce seguem exatamente como antes (2o passe e no-op)."""
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(CTE_XML, "cte.xml"),
        headers=auth_headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["uf"] is None
    assert data["valor_icms"] is None
    detail = await api_client.get(
        f"/api/v1/fiscal/documentos/{data['id']}", headers=auth_headers
    )
    assert detail.json()["itens"] == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_router.py -v -k "detalhada or cte_nao_gera"`
Expected: FAIL (KeyError `uf` / `itens` — schema ainda não expõe)

- [ ] **Step 3: Schemas**

Em `apps/api/app/modules/fiscal/schemas.py`:

(a) em `DocumentoFiscalRead`, após `data_emissao`, adicionar:

```python
    uf: str | None = None
    chave_dv_valida: bool | None = None
    valor_icms: Decimal | None = None
    valor_ipi: Decimal | None = None
    valor_pis: Decimal | None = None
    valor_cofins: Decimal | None = None
    obra_id: int | None = None
```

(b) ao final do arquivo:

```python
class DocumentoFiscalItemRead(BaseModel):
    """Item (<det>) da NF-e no detalhe do documento."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ordem: int
    codigo: str | None = None
    descricao: str | None = None
    ncm: str | None = None
    cfop: str | None = None
    unidade: str | None = None
    quantidade: Decimal | None = None
    valor_unitario: Decimal | None = None
    valor_total: Decimal | None = None


class DocumentoFiscalDetail(DocumentoFiscalRead):
    """Detalhe = metadados + itens (so NF-e/NFC-e tem itens)."""

    itens: list[DocumentoFiscalItemRead] = []
```

- [ ] **Step 4: Service**

Em `apps/api/app/modules/fiscal/service.py`:

(a) atualizar imports:

```python
from app.modules.fiscal.models import (
    STATUS_ENVIO_VALIDOS,
    DocumentoFiscal,
    DocumentoFiscalItem,
)
from app.modules.fiscal.parser import (
    FiscalParseError,
    parse_nfe_detalhes,
    parse_xml,
)
```

(b) em `import_xml`, logo após `parsed = parse_xml(xml_bytes)`:

```python
    # 2o passe: impostos/UF/itens (so NF-e/NFC-e; None para os demais).
    detalhes = (
        parse_nfe_detalhes(xml_bytes)
        if parsed.tipo in {"nfe", "nfce"}
        else None
    )
```

(c) no construtor `DocumentoFiscal(...)` do mesmo `import_xml`, adicionar após `data_emissao=parsed.data_emissao,`:

```python
        uf=detalhes.uf if detalhes else None,
        chave_dv_valida=detalhes.chave_dv_valida if detalhes else None,
        valor_icms=detalhes.valor_icms if detalhes else None,
        valor_ipi=detalhes.valor_ipi if detalhes else None,
        valor_pis=detalhes.valor_pis if detalhes else None,
        valor_cofins=detalhes.valor_cofins if detalhes else None,
```

(d) trocar o bloco `db.add(doc)` / `try: await db.commit()` por (itens entram na MESMA transação — o rollback do race de duplicata desfaz tudo junto):

```python
    db.add(doc)
    try:
        await db.flush()  # materializa doc.id para os itens
        if detalhes:
            for item in detalhes.itens:
                db.add(
                    DocumentoFiscalItem(
                        documento_id=doc.id,
                        ordem=item.ordem,
                        codigo=item.codigo,
                        descricao=item.descricao,
                        ncm=item.ncm,
                        cfop=item.cfop,
                        unidade=item.unidade,
                        quantidade=item.quantidade,
                        valor_unitario=item.valor_unitario,
                        valor_total=item.valor_total,
                    )
                )
        await db.commit()
```

(o `except IntegrityError` existente permanece idêntico)

(e) no `metadata` do `_record_audit(action="create", ...)` do `import_xml`, adicionar a linha:

```python
            "itens_count": len(detalhes.itens) if detalhes else 0,
```

(f) nova função após `get_documento`:

```python
async def list_itens(
    db: AsyncSession, documento_id: int
) -> Sequence[DocumentoFiscalItem]:
    stmt = (
        select(DocumentoFiscalItem)
        .where(DocumentoFiscalItem.documento_id == documento_id)
        .order_by(DocumentoFiscalItem.ordem)
    )
    result = await db.scalars(stmt)
    return result.all()
```

(g) adicionar `"list_itens"` ao `__all__`.

- [ ] **Step 5: Router**

Em `apps/api/app/modules/fiscal/router.py`:

(a) importar `DocumentoFiscalDetail`, `DocumentoFiscalItemRead` no bloco de schemas e `list_itens` no bloco do service.

(b) trocar o endpoint `get_endpoint` por:

```python
@router.get("/documentos/{doc_id}", response_model=DocumentoFiscalDetail)
async def get_endpoint(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
) -> DocumentoFiscalDetail:
    doc = await get_documento(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    itens = await list_itens(db, doc_id)
    base = DocumentoFiscalRead.model_validate(doc).model_dump()
    return DocumentoFiscalDetail(
        **base,
        itens=[DocumentoFiscalItemRead.model_validate(i) for i in itens],
    )
```

- [ ] **Step 6: Rodar e ver passar (suíte fiscal inteira)**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_router.py tests/test_fiscal_parser.py -v`
Expected: PASS

- [ ] **Step 7: Lint + commit**

```bash
cd apps/api && uv run --extra dev ruff check .
git add apps/api/app/modules/fiscal/service.py apps/api/app/modules/fiscal/schemas.py apps/api/app/modules/fiscal/router.py apps/api/tests/test_fiscal_router.py
git commit -m "feat(fiscal): upload NF-e persiste impostos/UF/itens; detalhe com itens"
```

---

### Task 5: Filtros de listagem + vínculo com obra

**Files:**
- Modify: `apps/api/app/modules/fiscal/service.py`
- Modify: `apps/api/app/modules/fiscal/schemas.py`
- Modify: `apps/api/app/modules/fiscal/router.py`
- Test: `apps/api/tests/test_fiscal_router.py`

**Interfaces:**
- Consumes: coluna `obra_id` (Task 3); model `Obra` de `app.modules.obras.models` (tabela `obras_obra`; campos `id`, `codigo`, `nome`).
- Produces: `list_documentos` ganha kwargs `emitida_de: date | None`, `emitida_ate: date | None`, `obra_id: int | None`, `valor_min: Decimal | None`, `valor_max: Decimal | None`; `update_documento` ganha kwarg `obra_id` com sentinela `_UNSET`; `DocumentoFiscalUpdate` ganha campo `obra_id: int | None`. Consumidos pela Task 7 (UI).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `apps/api/tests/test_fiscal_router.py`:

```python
@pytest.mark.asyncio
async def test_filtros_periodo_valor_e_obra(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
):
    """NFE_44 (2024-04-15, 15750.50) e NFE_DETALHADA (2024-04-22, 25000.00):
    filtros de periodo, valor e obra isolam cada uma."""
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, "a.xml"),
        headers=auth_headers,
    )
    r2 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_DETALHADA_XML, "b.xml"),
        headers=auth_headers,
    )
    assert r1.status_code == 201 and r2.status_code == 201
    id1, id2 = r1.json()["id"], r2.json()["id"]

    # Periodo: so a detalhada foi emitida a partir de 2024-04-20.
    r = await api_client.get(
        "/api/v1/fiscal/documentos",
        params={"emitida_de": "2024-04-20"},
        headers=auth_headers,
    )
    ids = [d["id"] for d in r.json()]
    assert id2 in ids and id1 not in ids

    # Periodo com teto: so a NFE_44 ate 2024-04-18.
    r = await api_client.get(
        "/api/v1/fiscal/documentos",
        params={"emitida_ate": "2024-04-18"},
        headers=auth_headers,
    )
    ids = [d["id"] for d in r.json()]
    assert id1 in ids and id2 not in ids

    # Valor minimo: so a detalhada passa de 20000.
    r = await api_client.get(
        "/api/v1/fiscal/documentos",
        params={"valor_min": "20000"},
        headers=auth_headers,
    )
    ids = [d["id"] for d in r.json()]
    assert id2 in ids and id1 not in ids

    # Obra: cria uma obra, vincula so a detalhada, filtra por ela.
    obra = Obra(codigo="OB-100", nome="Rodovia Teste")
    db_session.add(obra)
    await db_session.commit()

    patch = await api_client.patch(
        f"/api/v1/fiscal/documentos/{id2}",
        json={"obra_id": obra.id},
        headers=auth_headers,
    )
    assert patch.status_code == 200
    assert patch.json()["obra_id"] == obra.id

    r = await api_client.get(
        "/api/v1/fiscal/documentos",
        params={"obra_id": obra.id},
        headers=auth_headers,
    )
    ids = [d["id"] for d in r.json()]
    assert ids == [id2]


@pytest.mark.asyncio
async def test_patch_obra_inexistente_retorna_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, "a.xml"),
        headers=auth_headers,
    )
    r = await api_client.patch(
        f"/api/v1/fiscal/documentos/{r1.json()['id']}",
        json={"obra_id": 999999},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert "obra" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_patch_obra_null_desvincula(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
):
    obra = Obra(codigo="OB-101", nome="Ponte Teste")
    db_session.add(obra)
    await db_session.commit()
    r1 = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_44_XML, "a.xml"),
        headers=auth_headers,
    )
    doc_id = r1.json()["id"]
    await api_client.patch(
        f"/api/v1/fiscal/documentos/{doc_id}",
        json={"obra_id": obra.id},
        headers=auth_headers,
    )
    r = await api_client.patch(
        f"/api/v1/fiscal/documentos/{doc_id}",
        json={"obra_id": None},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["obra_id"] is None
```

Acrescentar ao topo do arquivo:

```python
from app.modules.obras.models import Obra
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_router.py -v -k "filtros or patch_obra"`
Expected: FAIL (filtros ignorados / `obra_id` não aceito no PATCH)

- [ ] **Step 3: Service — filtros**

Em `apps/api/app/modules/fiscal/service.py`:

(a) imports: acrescentar `from datetime import UTC, date, datetime, time, timedelta` (substituindo o import atual de datetime) e `from decimal import Decimal`; acrescentar `from app.modules.obras.models import Obra`.

(b) assinatura e corpo de `list_documentos` — adicionar os kwargs após `search`:

```python
    emitida_de: date | None = None,
    emitida_ate: date | None = None,
    obra_id: int | None = None,
    valor_min: Decimal | None = None,
    valor_max: Decimal | None = None,
```

e, no corpo, após o bloco do `search`:

```python
    if emitida_de is not None:
        stmt = stmt.where(
            DocumentoFiscal.data_emissao
            >= datetime.combine(emitida_de, time.min, tzinfo=UTC)
        )
    if emitida_ate is not None:
        # limite exclusivo no dia seguinte cobre qualquer horario/fuso
        stmt = stmt.where(
            DocumentoFiscal.data_emissao
            < datetime.combine(
                emitida_ate + timedelta(days=1), time.min, tzinfo=UTC
            )
        )
    if obra_id is not None:
        stmt = stmt.where(DocumentoFiscal.obra_id == obra_id)
    if valor_min is not None:
        stmt = stmt.where(DocumentoFiscal.valor_total >= valor_min)
    if valor_max is not None:
        stmt = stmt.where(DocumentoFiscal.valor_total <= valor_max)
```

- [ ] **Step 4: Service — `update_documento` com obra**

Ainda em `service.py`, adicionar o sentinela no nível do módulo (antes de `update_documento`):

```python
# Distingue "nao enviou obra_id" de "enviou obra_id=null" (desvincular).
_UNSET: Any = object()
```

e alterar `update_documento`: assinatura ganha `obra_id: Any = _UNSET,` (após `status_envio`) e, no corpo, antes de `if not changed:`:

```python
    if obra_id is not _UNSET and obra_id != doc.obra_id:
        if obra_id is not None:
            obra = await db.get(Obra, obra_id)
            if obra is None:
                raise ValueError(f"obra {obra_id} nao encontrada")
        changed["obra_id"] = {"from": doc.obra_id, "to": obra_id}
        doc.obra_id = obra_id
```

- [ ] **Step 5: Schema + router**

Em `schemas.py`, adicionar a `DocumentoFiscalUpdate` (após `status_envio`):

```python
    obra_id: int | None = None
```

Em `router.py`:

(a) `list_endpoint` — adicionar os params (após `search`) e repassar ao service:

```python
    emitida_de: date | None = Query(default=None),
    emitida_ate: date | None = Query(default=None),
    obra_id: int | None = Query(default=None, ge=1),
    valor_min: Decimal | None = Query(default=None, ge=0),
    valor_max: Decimal | None = Query(default=None, ge=0),
```

(repassar `emitida_de=emitida_de, emitida_ate=emitida_ate, obra_id=obra_id, valor_min=valor_min, valor_max=valor_max` na chamada `list_documentos`; imports novos no topo: `from datetime import date` e `from decimal import Decimal`)

(b) `update_endpoint` — repassar obra_id respeitando o sentinela:

```python
    try:
        kwargs: dict[str, Any] = {}
        if "obra_id" in payload.model_fields_set:
            kwargs["obra_id"] = payload.obra_id
        doc = await update_documento(
            db,
            doc_id,
            observacoes=payload.observacoes,
            status_envio=payload.status_envio,
            actor=current_user.email,
            **kwargs,
        )
```

- [ ] **Step 6: Rodar e ver passar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_router.py -v`
Expected: PASS

- [ ] **Step 7: Lint + commit**

```bash
cd apps/api && uv run --extra dev ruff check .
git add apps/api/app/modules/fiscal/
git add apps/api/tests/test_fiscal_router.py
git commit -m "feat(fiscal): filtros periodo/valor/obra + vinculo de nota a obra"
```

---

### Task 6: Reprocessamento de documentos legados

**Files:**
- Modify: `apps/api/app/modules/fiscal/service.py`
- Modify: `apps/api/app/modules/fiscal/router.py`
- Test: `apps/api/tests/test_fiscal_router.py`

**Interfaces:**
- Consumes: `parse_nfe_detalhes` (Task 2), `DocumentoFiscalItem` (Task 3), `list_itens` e `DocumentoFiscalDetail` (Task 4), `EditaisStorage.read(path) -> bytes` (existente).
- Produces: `reprocessar_documento(db, doc_id, *, storage, actor) -> DocumentoFiscal | None` (None = não achou; `ValueError` = tipo não suportado / detalhes ausentes; `FileNotFoundError`/`OSError` sobem para o router tratar); `POST /api/v1/fiscal/documentos/{id}/reprocessar` → `DocumentoFiscalDetail`.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `apps/api/tests/test_fiscal_router.py`:

```python
@pytest.mark.asyncio
async def test_reprocessar_preenche_detalhes_e_e_idempotente(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
):
    """Simula documento importado ANTES da feature: zera os campos novos
    direto no banco e reprocessa -- os detalhes voltam do XML no storage.
    Segunda chamada nao duplica itens (delete + re-insert)."""
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(NFE_DETALHADA_XML, "nfe.xml"),
        headers=auth_headers,
    )
    doc_id = r.json()["id"]

    # "Documento legado": apaga o resultado do 2o passe.
    doc = await db_session.get(DocumentoFiscal, doc_id)
    doc.uf = None
    doc.valor_icms = None
    doc.chave_dv_valida = None
    await db_session.execute(
        delete(DocumentoFiscalItem).where(
            DocumentoFiscalItem.documento_id == doc_id
        )
    )
    await db_session.commit()

    r = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/reprocessar",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uf"] == "SP"
    assert body["valor_icms"] == "3000.00"
    assert len(body["itens"]) == 2

    # Idempotencia: reprocessar de novo mantem 2 itens (nao 4).
    r = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/reprocessar",
        headers=auth_headers,
    )
    assert len(r.json()["itens"]) == 2


@pytest.mark.asyncio
async def test_reprocessar_tipo_nao_suportado_retorna_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    r = await api_client.post(
        "/api/v1/fiscal/documentos",
        files=_upload_payload(CTE_XML, "cte.xml"),
        headers=auth_headers,
    )
    doc_id = r.json()["id"]
    r = await api_client.post(
        f"/api/v1/fiscal/documentos/{doc_id}/reprocessar",
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_reprocessar_documento_inexistente_retorna_404(
    api_client: AsyncClient, auth_headers: dict[str, str]
):
    r = await api_client.post(
        "/api/v1/fiscal/documentos/999999/reprocessar",
        headers=auth_headers,
    )
    assert r.status_code == 404
```

Acrescentar `delete` ao import de sqlalchemy no topo do arquivo de teste
(`from sqlalchemy import delete, func, select`).

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_router.py -v -k reprocessar`
Expected: FAIL com 404 (rota não existe) / `Method Not Allowed`

- [ ] **Step 3: Service**

Em `apps/api/app/modules/fiscal/service.py`, acrescentar `delete` ao import
(`from sqlalchemy import delete, desc, or_, select`) e adicionar após `reprocessar` a função (colocar após `list_itens`):

```python
async def reprocessar_documento(
    db: AsyncSession,
    doc_id: int,
    *,
    storage: EditaisStorage,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> DocumentoFiscal | None:
    """Re-executa o 2o passe (parse_nfe_detalhes) sobre o XML bruto do
    storage. Uso: enriquecer documentos importados antes da feature de
    detalhes. Idempotente: apaga e reinsere os itens.

    Levanta ValueError para tipos sem 2o passe; FileNotFoundError/OSError
    sobem se o XML sumiu do storage (o router converte em HTTP).
    """
    doc = await get_documento(db, doc_id)
    if doc is None:
        return None
    if doc.tipo not in {"nfe", "nfce"}:
        raise ValueError(
            f"reprocessamento so se aplica a nfe/nfce (tipo={doc.tipo!r})"
        )
    xml_bytes = await storage.read(doc.xml_path)
    detalhes = parse_nfe_detalhes(xml_bytes)
    if detalhes is None:
        raise ValueError("XML no storage nao e mais uma NF-e/NFC-e valida")

    doc.uf = detalhes.uf
    doc.chave_dv_valida = detalhes.chave_dv_valida
    doc.valor_icms = detalhes.valor_icms
    doc.valor_ipi = detalhes.valor_ipi
    doc.valor_pis = detalhes.valor_pis
    doc.valor_cofins = detalhes.valor_cofins
    await db.execute(
        delete(DocumentoFiscalItem).where(
            DocumentoFiscalItem.documento_id == doc.id
        )
    )
    for item in detalhes.itens:
        db.add(
            DocumentoFiscalItem(
                documento_id=doc.id,
                ordem=item.ordem,
                codigo=item.codigo,
                descricao=item.descricao,
                ncm=item.ncm,
                cfop=item.cfop,
                unidade=item.unidade,
                quantidade=item.quantidade,
                valor_unitario=item.valor_unitario,
                valor_total=item.valor_total,
            )
        )
    await db.commit()
    await db.refresh(doc)
    await _record_audit(
        db,
        action="reprocessar",
        resource_id=doc.id,
        actor=actor,
        metadata={
            "uf": doc.uf,
            "chave_dv_valida": doc.chave_dv_valida,
            "itens_count": len(detalhes.itens),
        },
    )
    return doc
```

Adicionar `"reprocessar_documento"` ao `__all__`.

- [ ] **Step 4: Router**

Em `apps/api/app/modules/fiscal/router.py`, importar `reprocessar_documento` no bloco do service e adicionar o endpoint (após `update_endpoint`):

```python
@router.post(
    "/documentos/{doc_id}/reprocessar",
    response_model=DocumentoFiscalDetail,
)
async def reprocessar_endpoint(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    storage: EditaisStorage = Depends(get_fiscal_storage),
    current_user: User = Depends(get_current_user),
) -> DocumentoFiscalDetail:
    """Re-extrai impostos/UF/itens do XML bruto (docs pre-feature)."""
    try:
        doc = await reprocessar_documento(
            db, doc_id, storage=storage, actor=current_user.email
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"XML nao encontrado no storage: {exc}",
        ) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    itens = await list_itens(db, doc_id)
    base = DocumentoFiscalRead.model_validate(doc).model_dump()
    return DocumentoFiscalDetail(
        **base,
        itens=[DocumentoFiscalItemRead.model_validate(i) for i in itens],
    )
```

- [ ] **Step 5: Rodar e ver passar + suíte completa**

Run: `cd apps/api && uv run --extra dev pytest tests/test_fiscal_router.py tests/test_fiscal_parser.py -v && uv run --extra dev pytest -q`
Expected: PASS em tudo

- [ ] **Step 6: Lint + commit**

```bash
cd apps/api && uv run --extra dev ruff check .
git add apps/api/app/modules/fiscal/ apps/api/tests/test_fiscal_router.py
git commit -m "feat(fiscal): POST /documentos/{id}/reprocessar para docs legados"
```

---

### Task 7: UI — filtros, colunas novas e página de detalhe

**Files:**
- Modify: `apps/web/src/app/(dashboard)/fiscal/documentos/page.tsx`
- Create: `apps/web/src/app/(dashboard)/fiscal/documentos/[id]/page.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/fiscal/documentos` (params novos `emitida_de`, `emitida_ate`, `obra_id`), `GET /api/v1/fiscal/documentos/{id}` (agora com `itens`), `PATCH /api/v1/fiscal/documentos/{id}` (`obra_id`), `GET /api/v1/obras` (lista `{id, codigo, nome}` — router já existe); helper `apiFetch<T>(path, init?)` de `@/lib/api`.
- Produces: nada consumido por outras tasks (folha).

**Antes de editar: leia o arquivo `page.tsx` inteiro** — ele é um server component com `searchParams`, helpers `formatCnpj/formatDate/formatBRL` e uma função `fetchDocumentos(params)`. As mudanças abaixo seguem esse padrão.

- [ ] **Step 1: Filtros e colunas na listagem**

Em `apps/web/src/app/(dashboard)/fiscal/documentos/page.tsx`:

(a) no type `DocumentoFiscal`, adicionar os campos:

```tsx
  uf: string | null;
  chave_dv_valida: boolean | null;
  valor_icms: string | null;
  obra_id: number | null;
```

(b) estender a assinatura de `fetchDocumentos` com `emitida_de?: string; emitida_ate?: string; obra_id?: string;` e repassar os três como query params exatamente como os filtros existentes (`tipo`, `status_envio`, `search`) são montados.

(c) ler os novos params do `searchParams` da page e repassar ao `fetchDocumentos`; no formulário de filtros (mesmo `<form>` GET dos filtros atuais), adicionar:

```tsx
<input
  type="date"
  name="emitida_de"
  defaultValue={emitida_de ?? ""}
  className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
/>
<input
  type="date"
  name="emitida_ate"
  defaultValue={emitida_ate ?? ""}
  className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
/>
```

(d) na tabela, adicionar coluna "UF" (`{doc.uf ?? "—"}`) e trocar a célula do número/chave para linkar ao detalhe:

```tsx
<Link
  href={`/fiscal/documentos/${doc.id}`}
  className="font-medium text-slate-900 hover:underline"
>
  {doc.numero ?? doc.chave_acesso ?? `#${doc.id}`}
</Link>
```

(e) quando `doc.chave_dv_valida === false`, exibir junto ao número um badge de atenção:

```tsx
{doc.chave_dv_valida === false && (
  <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
    DV inválido
  </span>
)}
```

- [ ] **Step 2: Página de detalhe**

Criar `apps/web/src/app/(dashboard)/fiscal/documentos/[id]/page.tsx`:

```tsx
import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type ItemNota = {
  id: number;
  ordem: number;
  codigo: string | null;
  descricao: string | null;
  ncm: string | null;
  cfop: string | null;
  unidade: string | null;
  quantidade: string | null;
  valor_unitario: string | null;
  valor_total: string | null;
};

type DocumentoDetail = {
  id: number;
  tipo: string;
  chave_acesso: string | null;
  numero: string | null;
  serie: string | null;
  emitente_cnpj: string | null;
  emitente_nome: string | null;
  destinatario_cnpj: string | null;
  destinatario_nome: string | null;
  valor_total: string | null;
  data_emissao: string | null;
  uf: string | null;
  chave_dv_valida: boolean | null;
  valor_icms: string | null;
  valor_ipi: string | null;
  valor_pis: string | null;
  valor_cofins: string | null;
  obra_id: number | null;
  status_envio: string;
  observacoes: string | null;
  itens: ItemNota[];
};

type Obra = { id: number; codigo: string; nome: string };

export const dynamic = "force-dynamic";

function brl(v: string | null): string {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return v;
  return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

export default async function DocumentoFiscalDetalhePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [doc, obras] = await Promise.all([
    apiFetch<DocumentoDetail>(`/api/v1/fiscal/documentos/${id}`),
    apiFetch<Obra[]>(`/api/v1/obras`),
  ]);

  async function vincularObra(formData: FormData) {
    "use server";
    const raw = formData.get("obra_id");
    const obra_id = raw ? Number(raw) : null;
    await apiFetch(`/api/v1/fiscal/documentos/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ obra_id }),
    });
    revalidatePath(`/fiscal/documentos/${id}`);
  }

  const impostos: Array<[string, string | null]> = [
    ["ICMS", doc.valor_icms],
    ["IPI", doc.valor_ipi],
    ["PIS", doc.valor_pis],
    ["COFINS", doc.valor_cofins],
  ];

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/fiscal/documentos"
          className="text-sm text-slate-500 hover:underline"
        >
          ← Documentos fiscais
        </Link>
        <h1 className="mt-1 text-2xl font-semibold text-slate-900">
          {doc.tipo.toUpperCase()} {doc.numero ?? ""}{" "}
          {doc.serie ? `/ Série ${doc.serie}` : ""}
        </h1>
        <p className="mt-1 font-mono text-xs text-slate-500">
          {doc.chave_acesso ?? "sem chave de acesso"}
          {doc.chave_dv_valida === false && (
            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">
              DV inválido
            </span>
          )}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs text-slate-500">Valor total</p>
          <p className="text-lg font-semibold">{brl(doc.valor_total)}</p>
        </div>
        {impostos.map(([nome, valor]) => (
          <div
            key={nome}
            className="rounded-xl border border-slate-200 bg-white p-4"
          >
            <p className="text-xs text-slate-500">{nome}</p>
            <p className="text-lg font-semibold">{brl(valor)}</p>
          </div>
        ))}
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs text-slate-500">UF</p>
          <p className="text-lg font-semibold">{doc.uf ?? "—"}</p>
        </div>
      </div>

      <form
        action={vincularObra}
        className="flex items-end gap-3 rounded-xl border border-slate-200 bg-white p-4"
      >
        <label className="flex-1 text-sm">
          <span className="mb-1 block text-xs text-slate-500">
            Obra vinculada
          </span>
          <select
            name="obra_id"
            defaultValue={doc.obra_id ?? ""}
            className="w-full rounded-lg border border-slate-200 px-3 py-2"
          >
            <option value="">— sem obra —</option>
            {obras.map((o) => (
              <option key={o.id} value={o.id}>
                {o.codigo} · {o.nome}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          Salvar
        </button>
      </form>

      <div className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-900">
          Itens ({doc.itens.length})
        </h2>
        {doc.itens.length === 0 ? (
          <p className="px-4 py-6 text-sm text-slate-500">
            Sem itens extraídos — use “Reprocessar” na API para notas
            importadas antes da extração de itens, ou o tipo não suporta
            itens (NFS-e, CT-e, CF-e, Baixa).
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs text-slate-500">
                  <th className="px-4 py-2">#</th>
                  <th className="px-4 py-2">Código</th>
                  <th className="px-4 py-2">Descrição</th>
                  <th className="px-4 py-2">NCM</th>
                  <th className="px-4 py-2">CFOP</th>
                  <th className="px-4 py-2 text-right">Qtd</th>
                  <th className="px-4 py-2 text-right">Vlr unit.</th>
                  <th className="px-4 py-2 text-right">Total</th>
                </tr>
              </thead>
              <tbody>
                {doc.itens.map((item) => (
                  <tr key={item.id} className="border-b border-slate-50">
                    <td className="px-4 py-2 text-slate-500">{item.ordem}</td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {item.codigo ?? "—"}
                    </td>
                    <td className="px-4 py-2">{item.descricao ?? "—"}</td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {item.ncm ?? "—"}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {item.cfop ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {item.quantidade ?? "—"} {item.unidade ?? ""}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {brl(item.valor_unitario)}
                    </td>
                    <td className="px-4 py-2 text-right font-medium">
                      {brl(item.valor_total)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
```

Observação: se o Next do repo for < 15, `params` não é Promise — nesse caso a assinatura é `{ params }: { params: { id: string } }` e remove-se o `await`. Confira como as outras rotas dinâmicas do repo tipam `params` (ex.: `src/app/(dashboard)/licitacoes/[id]/page.tsx` se existir) e siga o mesmo padrão.

- [ ] **Step 3: Build + lint do web**

Run: `cd apps/web && npm run lint && npm run build`
Expected: build OK, sem erros de tipo/lint

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/app/\(dashboard\)/fiscal/documentos/
git commit -m "feat(web/fiscal): filtros por periodo/obra, badge DV e detalhe da NF-e com itens"
```

---

## Verificação final (após a última task)

```bash
cd apps/api && uv run --extra dev pytest -q && uv run --extra dev ruff check .
cd ../web && npm run lint && npm run build
```

Expected: suíte completa verde (565+ testes), ruff limpo, build web OK.

## Riscos e decisões registradas

1. **Não criei tabela `fiscal_notas_fiscais`**: o spec original da squad sugeria `NotaFiscal` nova, mas o repo já tem `fiscal_documentos` com upload/dedup/audit funcionando (PR #12). Estender evita duplicar dedup e trilha de auditoria. O "detalhe NF-e" vira colunas + tabela filha.
2. **DV informativo, nunca bloqueante**: a fixture legada `NFE_44_XML` tem DV inválido; rejeitar quebraria 565 testes e criaria falso-positivo com XMLs de homologação. Flag `chave_dv_valida` + badge na UI.
3. **`down_revision` da migration**: head era `b8c9d0e1f2a3` na escrita do plano; squads paralelas podem mudá-lo — o implementador DEVE rodar `alembic heads` antes (está no step).
4. **Filtro de período com fuso**: `data_emissao` é tz-aware (`dhEmi` vem com offset -03:00); os limites usam UTC e o teto é exclusivo no dia seguinte, então datas de teste têm margem de dias — sem flakiness de fuso.
5. **Itens na mesma transação do upload**: `flush()` para obter `doc.id`; o handler de race (IntegrityError) existente faz rollback de nota + itens juntos.
