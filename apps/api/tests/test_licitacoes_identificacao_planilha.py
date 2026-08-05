"""Tests da identificacao de planilha orcamentaria (Captador Squad 2).

Cobre o scoring da secao 8 do Projeto Tecnico: extensao + nome +
conteudo interno, com fallback gracioso para bytes corrompidos.
"""
from __future__ import annotations

import io

from app.modules.licitacoes.identificacao_planilha import (
    EXTENSOES_ELEGIVEIS,
    LIMIAR_PRINCIPAL,
    classificar_anexo,
    normalizar,
    score_conteudo,
    score_nome,
)


def _xlsx_bytes(primeira_linha: list[str]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(primeira_linha)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ods_bytes(primeira_linha: list[str]) -> bytes:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    table = Table(name="Plan1")
    row = TableRow()
    for valor in primeira_linha:
        cell = TableCell()
        cell.addElement(P(text=valor))
        row.addElement(cell)
    table.addElement(row)
    doc.spreadsheet.addElement(table)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_normalizar_remove_acentos_e_separadores() -> None:
    assert normalizar("Orçamento_Sintético-2026") == "orcamento sintetico 2026"


def test_score_nome_pontua_10_por_termo() -> None:
    # "orcament" + "planilha" = 2 termos -> 20
    assert score_nome("Planilha_Orcamentaria.xlsx") == 20
    assert score_nome("edital_retificado.pdf") == 0


def test_score_conteudo_xlsx_acha_cabecalhos() -> None:
    data = _xlsx_bytes(["Item", "Unid", "Quant", "Preço Unitário", "Total"])
    # item, unid, quant, preco unitario, total -> 5 termos x 5 = 25
    assert score_conteudo("qualquer.xlsx", data) == 25


def test_score_conteudo_ods_acha_cabecalhos() -> None:
    data = _ods_bytes(["Item", "Quant", "Total"])
    assert score_conteudo("qualquer.ods", data) == 15


def test_score_conteudo_bytes_corrompidos_retorna_zero() -> None:
    assert score_conteudo("x.xlsx", b"nao sou um zip") == 0


def test_classificar_anexo_pdf_e_inelegivel() -> None:
    assert classificar_anexo("edital.pdf", b"%PDF") is None


def test_classificar_anexo_xls_legado_pontua_so_pelo_nome() -> None:
    # .xls e elegivel mas openpyxl nao le: conteudo ignorado.
    assert classificar_anexo("orcamento.xls", b"\xd0\xcf\x11\xe0lixo") == 10


def test_classificar_anexo_soma_nome_e_conteudo() -> None:
    data = _xlsx_bytes(["Item", "Total"])
    # nome: "orcament" -> 10; conteudo: item + total -> 10. Soma 20.
    assert classificar_anexo("orcamento.xlsx", data) == 20


def test_classificar_anexo_sem_bytes_usa_so_nome() -> None:
    assert classificar_anexo("planilha_de_custos.xlsx", None) == 20


def test_constantes_exportadas() -> None:
    assert ".xlsx" in EXTENSOES_ELEGIVEIS
    assert ".ods" in EXTENSOES_ELEGIVEIS
    assert ".xls" in EXTENSOES_ELEGIVEIS
    assert LIMIAR_PRINCIPAL == 10
