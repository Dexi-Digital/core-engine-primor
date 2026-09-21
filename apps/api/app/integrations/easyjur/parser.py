"""Leitura do que o EasyJur devolve: HTML de processos, CSV de andamentos.

Funcoes puras -- sem rede, sem banco. Ficam separadas do `client.py`
porque sao a peca fragil: o EasyJur nao tem API JSON, entao qualquer
mudanca de layout deles quebra AQUI e em nenhum outro lugar.

**Por que dois formatos.** Medido em 19/09/2026:

- Processos (453) vem do HTML, 50 por pagina. Barato e correto.
- Andamentos (12 mil) viriam em 243 paginas de HTML; o export CSV
  entrega tudo numa requisicao. Mas o CSV deles tem dois defeitos, e
  este modulo existe em boa parte por causa deles:

  1. **Cabecalho com 30 rotulos para 29 valores**, deslocado entre as
     posicoes 1 e 8 -- o numero do processo sai sob "Tipo de Acao" e o
     cliente sob "Processo", em 100% das linhas. Por isso a leitura e
     POSICIONAL e o cabecalho e ignorado como fonte de significado.
  2. **Acento vira "?"** ("Janu?ria") em campos que o HTML traz
     corretos. A corrupcao se concentra nos campos do PROCESSO, que
     nao lemos daqui -- so as colunas de andamento sao consumidas.
"""
from __future__ import annotations

import csv
import html as html_lib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime


class FormatoInesperadoError(ValueError):
    """O arquivo nao tem a forma (quebrada) que sabemos ler.

    Levantada de proposito quando o EasyJur MUDA o export -- inclusive
    se for para conserta-lo. Um export consertado lido com o mapa do
    export quebrado produziria exatamente o estrago que o mapa existe
    para evitar, e em silencio.
    """


class SessaoSemFiltroError(ValueError):
    """Export devolveu so o cabecalho.

    O export le o filtro da sessao PHP: sem um `pesquisa=enviar` antes,
    na mesma sessao, ele responde 200 com zero linhas. Nao e base
    vazia -- e a mesma familia de armadilha do `acao_listagem`.
    """


@dataclass(frozen=True)
class ProcessoBruto:
    easyjur_id: int
    numero_cnj: str | None
    status: str | None
    area: str | None
    tribunal: str | None
    instancia: str | None
    comarca: str | None
    tipo_acao: str | None
    titulo: str | None
    risco: str | None
    fase_atual: str | None
    resultado: str | None
    cliente: str | None
    contrario: str | None
    grupos: tuple[str, ...]
    codigo_obra: str | None


@dataclass(frozen=True)
class AndamentoBruto:
    easyjur_id: int
    numero_cnj: str
    tipo: str | None
    status: str | None
    descricao: str | None
    data: date | None


_CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
_TAGS = re.compile(r"<[^>]+>")
_TOTAL = re.compile(r"(\d[\d\.]*)\s+Registros\s+Encontrados", re.IGNORECASE)
_OBRA = re.compile(r"-\s*z\s*(\d+)\s*$", re.IGNORECASE)


def _texto(fragmento: str) -> str:
    return " ".join(html_lib.unescape(_TAGS.sub(" ", fragmento)).split())


def _campo(tr: str, rotulo: str) -> str | None:
    """Valor apos `<b>Rotulo: </b>`, ate a proxima tag de fechamento.

    `None` quando o rotulo nao existe na linha: o EasyJur OMITE o campo
    nao preenchido em vez de renderiza-lo vazio, entao ausencia aqui e
    "o escritorio nao preencheu", nunca "o parser nao achou".
    """
    m = re.search(
        rf"<b[^>]*>\s*{re.escape(rotulo)}:\s*</b>(.*?)</(?:span|a|td)>", tr, re.S
    )
    if not m:
        return None
    return _texto(m.group(1)) or None


def codigo_obra_de_grupo(tag: str | None) -> str | None:
    """`"Januária - z231"` -> `"231"`.

    O `z` e marcador descartavel (ZAG). Validado em 19/09/2026 contra
    os locais de trabalho do Tangerino: os 5 codigos da base batem,
    cidade inclusive. A regra so descarta `z` e so neste formato -- no
    Tangerino existem `C008`/`C034`, onde a letra FAZ parte do codigo.
    """
    if not tag:
        return None
    m = _OBRA.search(tag.strip())
    return m.group(1) if m else None


def total_registros(html: str) -> int | None:
    m = _TOTAL.search(html)
    return int(m.group(1).replace(".", "")) if m else None


def parse_processos(html: str) -> list[ProcessoBruto]:
    processos: list[ProcessoBruto] = []
    for tr in re.findall(r"<tr[^>]*>.*?</tr>", html, re.S):
        id_m = re.search(r"alterar_processos\((\d+)\)", tr)
        if not id_m:
            continue
        cnj = _CNJ.search(tr)
        grupos = tuple(
            g
            for g in (
                _texto(x)
                for x in re.findall(r'class="table-item__grupo"[^>]*>([^<]*)<', tr)
            )
            if g
        )
        tribunal = _campo(tr, "Tribunal")
        processos.append(
            ProcessoBruto(
                easyjur_id=int(id_m.group(1)),
                numero_cnj=cnj.group(0) if cnj else None,
                status=_campo(tr, "Status"),
                area=_campo(tr, "Área"),
                # "TRT03 - Tribunal Regional ... - VARA X" -> so a sigla.
                tribunal=tribunal.split(" - ")[0] if tribunal else None,
                instancia=_campo(tr, "Instância"),
                comarca=_campo(tr, "Comarca"),
                tipo_acao=_campo(tr, "Tipo da Ação"),
                titulo=_campo(tr, "Título"),
                risco=_campo(tr, "Risco"),
                fase_atual=_campo(tr, "Fase atual"),
                resultado=_campo(tr, "Resultado"),
                cliente=_campo(tr, "Cliente"),
                contrario=_campo(tr, "Contrário"),
                grupos=grupos,
                codigo_obra=next(
                    (c for c in map(codigo_obra_de_grupo, grupos) if c), None
                ),
            )
        )
    return processos


# --- CSV de andamentos ------------------------------------------------------
#
# Posicoes REAIS dos valores (nao as do cabecalho, que esta errado):
_POS_ID = 0
_POS_CNJ = 8
_POS_TIPO = 24
_POS_STATUS = 25
_POS_DESCRICAO = 26
_POS_DATA = 27

_COLUNAS_CABECALHO = 30
_COLUNAS_LINHA = 29


def _data_br(valor: str) -> date | None:
    try:
        return datetime.strptime(valor.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_andamentos_csv(bruto: bytes) -> list[AndamentoBruto]:
    # latin-1 nunca falha ao decodificar, e e o que o export usa de
    # fato (o header HTTP declara ISO-8859-9, mas o conteudo e latin-1).
    linhas = list(csv.reader(io.StringIO(bruto.decode("latin-1")), delimiter=";"))
    linhas = [ln for ln in linhas if ln]
    if not linhas:
        raise FormatoInesperadoError("export vazio: nem cabecalho veio")

    cabecalho, dados = linhas[0], linhas[1:]
    if len(cabecalho) != _COLUNAS_CABECALHO:
        raise FormatoInesperadoError(
            f"cabecalho com {len(cabecalho)} colunas; o mapa posicional foi "
            f"validado para {_COLUNAS_CABECALHO}. O EasyJur mudou o export."
        )
    if not dados:
        raise SessaoSemFiltroError(
            "export devolveu so o cabecalho: faltou o `pesquisa=enviar` na "
            "mesma sessao antes de exportar"
        )

    andamentos: list[AndamentoBruto] = []
    for n, linha in enumerate(dados, start=2):
        if len(linha) != _COLUNAS_LINHA:
            raise FormatoInesperadoError(
                f"linha {n} com {len(linha)} colunas; esperadas {_COLUNAS_LINHA}. "
                "Se o EasyJur consertou o export, o mapa posicional precisa "
                "ser refeito -- nao reinterpretado."
            )
        cnj = linha[_POS_CNJ].strip()
        if not _CNJ.fullmatch(cnj):
            raise FormatoInesperadoError(
                f"linha {n}: posicao {_POS_CNJ} nao e um numero CNJ ({cnj!r}). "
                "O deslocamento das colunas mudou."
            )
        andamentos.append(
            AndamentoBruto(
                easyjur_id=int(linha[_POS_ID]),
                numero_cnj=cnj,
                tipo=linha[_POS_TIPO].strip() or None,
                status=linha[_POS_STATUS].strip() or None,
                descricao=linha[_POS_DESCRICAO].strip() or None,
                data=_data_br(linha[_POS_DATA]),
            )
        )
    return andamentos
