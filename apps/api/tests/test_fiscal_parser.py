"""Testes do parser fiscal: detec\u00e7\u00e3o de tipo + extra\u00e7\u00e3o de metadata."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.fiscal.parser import (
    FiscalParseError,
    parse_nfe_detalhes,
    parse_xml,
    validar_chave_acesso,
)
from tests.fixtures.fiscal.samples import (
    ALL_SAMPLES,
    BAIXA_XML,
    CFE_XML,
    CTE_XML,
    NFCE_65_XML,
    NFE_44_XML,
    NFE_DETALHADA_XML,
    NFE_NITEM_DUPLICADO_XML,
    NFE_UF_INVALIDA_XML,
    NFSE_ABRASF_XML,
)


def test_parse_nfe_extrai_chave_e_valor():
    parsed = parse_xml(NFE_44_XML)
    assert parsed.tipo == "nfe"
    assert parsed.chave_acesso == "35240414200166000187550010000123451000000001"
    assert parsed.numero == "12345"
    assert parsed.serie == "1"
    assert parsed.emitente_cnpj == "14200166000187"
    assert parsed.emitente_nome == "Construtora Primor LTDA"
    assert parsed.destinatario_cnpj == "33000167000101"
    assert parsed.valor_total == Decimal("15750.50")
    assert parsed.data_emissao is not None
    assert parsed.xml_hash  # sha256 hex


def test_parse_nfce_detecta_modelo_65():
    """Diferenciacao critica: NF-e (mod 55) vs NFC-e (mod 65) -- a
    Domínio aceita os dois mas trata diferente. Nao podemos enviar
    NFC-e como NF-e."""
    parsed = parse_xml(NFCE_65_XML)
    assert parsed.tipo == "nfce"
    assert parsed.numero == "9999"
    # NFC-e tem CPF do consumidor (nao CNPJ).
    assert parsed.destinatario_cnpj == "12345678901"
    assert parsed.valor_total == Decimal("89.90")


def test_parse_cte_extrai_vTPrest():
    parsed = parse_xml(CTE_XML)
    assert parsed.tipo == "cte"
    assert parsed.chave_acesso == "35240414200166000187570010000055551000000001"
    assert parsed.numero == "5555"
    assert parsed.valor_total == Decimal("4500.00")


def test_parse_cfe_extrai_vCFe_e_data_compacta():
    """CF-e usa formato data 'yyyymmdd' (sem T) -- regressao do parser."""
    parsed = parse_xml(CFE_XML)
    assert parsed.tipo == "cfe"
    assert parsed.numero == "123450"
    assert parsed.serie == "900012345"
    assert parsed.valor_total == Decimal("250.00")
    assert parsed.data_emissao is not None
    assert parsed.data_emissao.year == 2024
    assert parsed.data_emissao.month == 4


def test_parse_nfse_abrasf():
    parsed = parse_xml(NFSE_ABRASF_XML)
    assert parsed.tipo == "nfse"
    assert parsed.numero == "2024000123"
    assert parsed.emitente_cnpj == "14200166000187"
    assert parsed.destinatario_cnpj == "33000167000101"
    assert parsed.valor_total == Decimal("3200.75")


def test_parse_baixa_parcela():
    parsed = parse_xml(BAIXA_XML)
    assert parsed.tipo == "baixa"
    assert parsed.numero == "P-001"
    assert parsed.emitente_cnpj == "14200166000187"
    assert parsed.valor_total == Decimal("1500.00")


def test_parse_xml_vazio_levanta_erro():
    with pytest.raises(FiscalParseError, match="vazio"):
        parse_xml(b"")


def test_parse_xml_malformado_levanta_erro():
    with pytest.raises(FiscalParseError, match="malformado"):
        parse_xml(b"<NFe><infNFe")


def test_parse_xml_tipo_desconhecido_levanta_erro():
    with pytest.raises(FiscalParseError, match="nao reconhecido"):
        parse_xml(b"<documento><foo>bar</foo></documento>")


def test_parse_xml_xxe_blocked_by_defusedxml():
    """Defusa: XXE/billion-laughs nao podem comprometer o parser.

    `defusedxml.ElementTree.fromstring` rejeita DOCTYPE com entidades
    -- regressao defensiva contra XML malicioso vindo de ERPs externos.
    """
    xxe = b"""<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<NFe><infNFe Id="NFe35"><test>&xxe;</test></infNFe></NFe>
"""
    with pytest.raises(FiscalParseError):
        parse_xml(xxe)


def test_parse_xml_hash_e_deterministico():
    """Hash SHA-256 e usado como chave de idempotencia para CF-e/Baixa
    onde nao ha chave_acesso. Tem que ser estavel byte-a-byte."""
    a = parse_xml(NFE_44_XML)
    b = parse_xml(NFE_44_XML)
    assert a.xml_hash == b.xml_hash

    c = parse_xml(CTE_XML)
    assert c.xml_hash != a.xml_hash


@pytest.mark.parametrize("tipo,xml", list(ALL_SAMPLES.items()))
def test_parse_todos_os_6_tipos_dominio(tipo: str, xml: bytes):
    """Smoke test: cada um dos 6 tipos suportados pela Dominio
    (NF-e, NFC-e, CT-e, CF-e, NFS-e, Baixa) deve parsear sem erro
    e retornar `parsed.tipo == tipo` esperado."""
    parsed = parse_xml(xml)
    assert parsed.tipo == tipo
    assert parsed.xml_hash


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


def test_parse_nfe_detalhes_sanitiza_nitem_duplicado():
    """Emissor malformado manda dois <det nItem="1">. O parser deve
    reatribuir a ordem posicionalmente para nao violar o
    UNIQUE(documento_id, ordem) no upload."""
    det = parse_nfe_detalhes(NFE_NITEM_DUPLICADO_XML)
    assert det is not None
    assert len(det.itens) == 2
    ordens = [item.ordem for item in det.itens]
    assert len(set(ordens)) == 2
    assert det.itens[0].ordem == 1
    assert det.itens[1].ordem == 2


def test_parse_nfe_detalhes_uf_invalida_cai_no_fallback_da_chave():
    """<UF>INVALIDA</UF> nao esta no whitelist _CUF_UF.values(); o
    parser deve descartar o texto arbitrario e manter o cUF da chave
    (35 -> SP) em vez de gravar algo que estoura String(2) no Postgres."""
    det = parse_nfe_detalhes(NFE_UF_INVALIDA_XML)
    assert det is not None
    assert det.uf == "SP"


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
