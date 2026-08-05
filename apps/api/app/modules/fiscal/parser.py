"""Parser de XMLs fiscais (NF-e/NFS-e/NFC-e/CT-e/CF-e/Baixa).

Decisoes:
  - `defusedxml` em vez de `xml.etree`: defesa contra XXE/billion laughs.
    XMLs vem de fontes externas (ERPs, prefeituras), nao podemos confiar.
  - Detectamos o tipo pela tag raiz + namespace; isso evita pedir o tipo
    explicito pelo cliente -- so manda o XML.
  - Extraimos so o minimo para indexacao + UI (chave, partes, valor,
    data). Para campos especificos (CFOP, alíquotas, itens), o XML bruto
    fica no storage e pode ser re-processado depois.
  - Falhas de parse caem em `FiscalParseError` com mensagem util para a
    UI -- nao deixamos exception "raw" do XML escapar.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException


class FiscalParseError(ValueError):
    """XML invalido, vazio, ou sem campos essenciais."""


@dataclass
class ParsedDocumento:
    """Metadados extraidos do XML para popular DocumentoFiscal."""

    tipo: str
    chave_acesso: str | None
    numero: str | None
    serie: str | None
    emitente_cnpj: str | None
    emitente_nome: str | None
    destinatario_cnpj: str | None
    destinatario_nome: str | None
    valor_total: Decimal | None
    data_emissao: datetime | None
    xml_hash: str


# Namespaces canonicos. Diferentes prefeituras emitem NFS-e com
# namespaces ligeiramente diferentes -- entao casamos por sufixo da tag
# (`localname`) em vez de match exato.
_NS = {
    "nfe": "http://www.portalfiscal.inf.br/nfe",
    "cte": "http://www.portalfiscal.inf.br/cte",
    "cfe": "http://www.fazenda.sp.gov.br/sat",
    "abrasf": "http://www.abrasf.org.br/nfse.xsd",
}


def _local(tag: str) -> str:
    """Extrai o localname de `{ns}Tag` -> `Tag`."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_first(root: ET.Element, *names: str) -> ET.Element | None:
    """Busca recursiva pelo primeiro elemento com localname em `names`."""
    targets = set(names)
    if _local(root.tag) in targets:
        return root
    for child in root.iter():
        if _local(child.tag) in targets:
            return child
    return None


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    txt = el.text.strip()
    return txt or None


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _datetime_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    # NF-e/CT-e: 2024-03-15T10:30:00-03:00 ou 2024-03-15T10:30:00
    # NFS-e: 2024-03-15T10:30:00 ou 2024-03-15
    # SEFAZ-SP CF-e: 20240315103000 (yyyymmddhhmmss)
    if re.fullmatch(r"\d{14}", value):
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S")
        except ValueError:
            return None
    if re.fullmatch(r"\d{8}", value):
        try:
            return datetime.strptime(value, "%Y%m%d")
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _detect_tipo(root: ET.Element) -> str:
    """Detecta o tipo pelo localname da raiz + filhos imediatos.

    Mapeamento (compatibilidade com leiautes da Domínio):
      - <NFe>/<nfeProc> + infNFe                        -> nfe
      - <NFe>/<nfeProc> com modelo 65 (NFC-e)           -> nfce
      - <CTe>/<cteProc>                                  -> cte
      - <CFe>/<CFeCanc>                                  -> cfe
      - <CompNfse>/<EnviarLoteRpsEnvio>/<Nfse>           -> nfse
      - <Baixas>                                         -> baixa
    """
    tag = _local(root.tag)
    if tag in {"NFe", "nfeProc"}:
        # NFC-e usa modelo 65 dentro de <ide><mod>. Fallback NF-e.
        ide = _find_first(root, "ide")
        if ide is not None:
            mod = _find_first(ide, "mod")
            if _text(mod) == "65":
                return "nfce"
        return "nfe"
    if tag in {"CTe", "cteProc"}:
        return "cte"
    if tag in {"CFe", "CFeCanc"}:
        return "cfe"
    if tag in {"CompNfse", "Nfse", "EnviarLoteRpsEnvio", "ConsultarNfseResposta"}:
        return "nfse"
    # NFS-e Nacional
    if "nfse" in tag.lower():
        return "nfse"
    if tag in {"Baixas", "BaixaParcela", "Baixa"}:
        return "baixa"
    # Fallback: procura tags caracteristicas em qualquer lugar.
    if _find_first(root, "infNFe") is not None:
        ide = _find_first(root, "ide")
        if ide is not None and _text(_find_first(ide, "mod")) == "65":
            return "nfce"
        return "nfe"
    if _find_first(root, "infCte") is not None:
        return "cte"
    if _find_first(root, "InfNfse", "infNfse") is not None:
        return "nfse"
    raise FiscalParseError(
        "Tipo de documento fiscal nao reconhecido (raiz: "
        f"{tag!r}). Suportados: NF-e, NFC-e, NFS-e, CT-e, CF-e, Baixa."
    )


def _parse_nfe_like(root: ET.Element) -> ParsedDocumento:
    """NF-e (modelo 55) e NFC-e (modelo 65) -- mesma estrutura."""
    inf = _find_first(root, "infNFe")
    if inf is None:
        raise FiscalParseError("NF-e/NFC-e sem <infNFe>")
    chave = inf.get("Id", "")
    if chave.startswith("NFe"):
        chave = chave[3:]

    ide = _find_first(inf, "ide")
    emit = _find_first(inf, "emit")
    dest = _find_first(inf, "dest")
    total = _find_first(inf, "total")

    tipo = "nfe"
    if ide is not None and _text(_find_first(ide, "mod")) == "65":
        tipo = "nfce"

    valor: Decimal | None = None
    if total is not None:
        icms_tot = _find_first(total, "ICMSTot")
        if icms_tot is not None:
            valor = _decimal(_text(_find_first(icms_tot, "vNF")))

    return ParsedDocumento(
        tipo=tipo,
        chave_acesso=chave or None,
        numero=_text(_find_first(ide, "nNF")) if ide is not None else None,
        serie=_text(_find_first(ide, "serie")) if ide is not None else None,
        emitente_cnpj=(
            _text(_find_first(emit, "CNPJ")) if emit is not None else None
        ),
        emitente_nome=(
            _text(_find_first(emit, "xNome")) if emit is not None else None
        ),
        destinatario_cnpj=(
            _text(_find_first(dest, "CNPJ"))
            or _text(_find_first(dest, "CPF"))
            if dest is not None
            else None
        ),
        destinatario_nome=(
            _text(_find_first(dest, "xNome")) if dest is not None else None
        ),
        valor_total=valor,
        data_emissao=_datetime_iso(
            _text(_find_first(ide, "dhEmi")) if ide is not None else None
        ),
        xml_hash="",  # preenchido pelo caller
    )


def _parse_cte(root: ET.Element) -> ParsedDocumento:
    inf = _find_first(root, "infCte")
    if inf is None:
        raise FiscalParseError("CT-e sem <infCte>")
    chave = inf.get("Id", "")
    if chave.startswith("CTe"):
        chave = chave[3:]

    ide = _find_first(inf, "ide")
    emit = _find_first(inf, "emit")
    dest = _find_first(inf, "dest")
    vprest = _find_first(inf, "vPrest")

    return ParsedDocumento(
        tipo="cte",
        chave_acesso=chave or None,
        numero=_text(_find_first(ide, "nCT")) if ide is not None else None,
        serie=_text(_find_first(ide, "serie")) if ide is not None else None,
        emitente_cnpj=(
            _text(_find_first(emit, "CNPJ")) if emit is not None else None
        ),
        emitente_nome=(
            _text(_find_first(emit, "xNome")) if emit is not None else None
        ),
        destinatario_cnpj=(
            _text(_find_first(dest, "CNPJ"))
            or _text(_find_first(dest, "CPF"))
            if dest is not None
            else None
        ),
        destinatario_nome=(
            _text(_find_first(dest, "xNome")) if dest is not None else None
        ),
        valor_total=(
            _decimal(_text(_find_first(vprest, "vTPrest")))
            if vprest is not None
            else None
        ),
        data_emissao=_datetime_iso(
            _text(_find_first(ide, "dhEmi")) if ide is not None else None
        ),
        xml_hash="",
    )


def _parse_cfe(root: ET.Element) -> ParsedDocumento:
    inf = _find_first(root, "infCFe")
    if inf is None:
        # Algumas SEFAZs serializam CF-e direto sem infCFe.
        inf = root
    chave = inf.get("Id", "")
    if chave.startswith("CFe"):
        chave = chave[3:]

    ide = _find_first(inf, "ide")
    emit = _find_first(inf, "emit")
    dest = _find_first(inf, "dest")
    total = _find_first(inf, "total")

    valor: Decimal | None = None
    if total is not None:
        valor = _decimal(_text(_find_first(total, "vCFe")))

    return ParsedDocumento(
        tipo="cfe",
        chave_acesso=chave or None,
        numero=_text(_find_first(ide, "nCFe")) if ide is not None else None,
        serie=_text(_find_first(ide, "serieSAT")) if ide is not None else None,
        emitente_cnpj=(
            _text(_find_first(emit, "CNPJ")) if emit is not None else None
        ),
        emitente_nome=(
            _text(_find_first(emit, "xNome"))
            or _text(_find_first(emit, "xFant"))
            if emit is not None
            else None
        ),
        destinatario_cnpj=(
            _text(_find_first(dest, "CNPJ"))
            or _text(_find_first(dest, "CPF"))
            if dest is not None
            else None
        ),
        destinatario_nome=(
            _text(_find_first(dest, "xNome")) if dest is not None else None
        ),
        valor_total=valor,
        data_emissao=_datetime_iso(
            _text(_find_first(ide, "dEmi")) if ide is not None else None
        ),
        xml_hash="",
    )


def _parse_nfse(root: ET.Element) -> ParsedDocumento:
    """NFS-e ABRASF + Nacional. Layout varia por prefeitura, vamos por
    busca de tags caracteristicas independente do path."""
    inf = _find_first(root, "InfNfse", "infNfse", "InfNFSe", "infNFSe")
    if inf is None:
        raise FiscalParseError("NFS-e sem <InfNfse>")

    chave = _text(_find_first(inf, "CodigoVerificacao", "codigoVerificacao"))
    numero = _text(_find_first(inf, "Numero", "numero"))
    serie = _text(_find_first(inf, "Serie", "serie"))

    prest = _find_first(inf, "PrestadorServico", "Prestador", "prestadorServico")
    tom = _find_first(inf, "TomadorServico", "Tomador", "tomadorServico")
    # `Servico`/`ValoresServico` ficam expostos via _find_first em busca
    # direta dos campos abaixo (`ValorServicos`).

    return ParsedDocumento(
        tipo="nfse",
        chave_acesso=chave or numero,
        numero=numero,
        serie=serie,
        emitente_cnpj=(
            _text(_find_first(prest, "Cnpj", "CNPJ"))
            if prest is not None
            else None
        ),
        emitente_nome=(
            _text(_find_first(prest, "RazaoSocial", "razaoSocial"))
            if prest is not None
            else None
        ),
        destinatario_cnpj=(
            _text(_find_first(tom, "Cnpj", "CNPJ"))
            or _text(_find_first(tom, "Cpf", "CPF"))
            if tom is not None
            else None
        ),
        destinatario_nome=(
            _text(_find_first(tom, "RazaoSocial", "razaoSocial"))
            if tom is not None
            else None
        ),
        valor_total=_decimal(
            _text(_find_first(inf, "ValorServicos", "valorServicos"))
        ),
        data_emissao=_datetime_iso(
            _text(_find_first(inf, "DataEmissao", "dataEmissao"))
        ),
        xml_hash="",
    )


def _parse_baixa(root: ET.Element) -> ParsedDocumento:
    """Baixa de Parcela: leiaute Domínio. Campos minimos."""
    item = _find_first(root, "Baixa", "BaixaParcela", "Parcela")
    if item is None:
        item = root
    return ParsedDocumento(
        tipo="baixa",
        chave_acesso=None,
        numero=_text(_find_first(item, "Numero", "numero")),
        serie=None,
        emitente_cnpj=_text(_find_first(item, "Cnpj", "CNPJ")),
        emitente_nome=_text(_find_first(item, "RazaoSocial", "razaoSocial")),
        destinatario_cnpj=None,
        destinatario_nome=None,
        valor_total=_decimal(
            _text(_find_first(item, "Valor", "valor", "ValorBaixa"))
        ),
        data_emissao=_datetime_iso(
            _text(_find_first(item, "DataBaixa", "dataBaixa", "Data"))
        ),
        xml_hash="",
    )


_PARSERS = {
    "nfe": _parse_nfe_like,
    "nfce": _parse_nfe_like,
    "cte": _parse_cte,
    "cfe": _parse_cfe,
    "nfse": _parse_nfse,
    "baixa": _parse_baixa,
}


def parse_xml(content: bytes) -> ParsedDocumento:
    """Parseia bytes do XML, retorna metadata + tipo detectado.

    Levanta `FiscalParseError` se o XML estiver malformado, vazio, ou
    sem os campos minimos para identificacao.
    """
    if not content or not content.strip():
        raise FiscalParseError("XML vazio")
    try:
        root = ET.fromstring(content)
    except DefusedXmlException as exc:
        # XXE / billion-laughs / DOCTYPE com entidades externas. Rejeitamos
        # com mensagem generica para nao vazar detalhes de payload.
        raise FiscalParseError(
            f"XML rejeitado por seguranca (entidades externas): {exc}"
        ) from exc
    except ET.ParseError as exc:
        raise FiscalParseError(f"XML malformado: {exc}") from exc

    tipo = _detect_tipo(root)
    parsed = _PARSERS[tipo](root)
    parsed.xml_hash = hashlib.sha256(content).hexdigest()
    return parsed


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
