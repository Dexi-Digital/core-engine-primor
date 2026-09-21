"""Parser do EasyJur -- HTML para processos, CSV para andamentos.

As fixtures sao a estrutura REAL do EasyJur, anonimizada: nomes
ficticios, CNJ com digitos trocados, ids falsos. Processo trabalhista
identifica quem processou a empresa, e historico de git e permanente.

A anonimizacao NAO corrige os defeitos do export deles: o CSV da
fixture tem cabecalho de 30 colunas para linhas de 29 e acento
corrompido, exatamente como o real. Fixture consertada testaria um
arquivo que nao existe.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.integrations.easyjur import parser

FIX = Path(__file__).parent / "fixtures" / "easyjur"


@pytest.fixture(scope="module")
def html() -> str:
    return (FIX / "processos_pagina.html").read_text()


@pytest.fixture(scope="module")
def csv_bytes() -> bytes:
    return (FIX / "andamentos_export.csv").read_bytes()


# --- processos (HTML) -------------------------------------------------------


def test_extrai_uma_linha_por_processo(html: str) -> None:
    assert len(parser.parse_processos(html)) == 3


def test_campos_de_alta_cobertura(html: str) -> None:
    p = parser.parse_processos(html)[0]
    assert p.easyjur_id == 90000001
    assert p.numero_cnj == "0000001-01.2025.5.03.0001"
    assert p.status == "Ativo"
    assert p.area == "Trabalhista"
    assert p.tribunal == "TRT03"
    assert p.instancia == "1º Grau"
    assert p.comarca == "Januária - MG"
    assert p.cliente == "FULANO DE TAL DA SILVA"
    assert p.contrario == "BELTRANA SOUZA LIMA"


def test_campo_ausente_vira_none_e_nao_string_vazia(html: str) -> None:
    """A diferenca entre "o escritorio nao preencheu" e "o parser nao
    achou". So 11% dos processos reais tem risco; tratar ausencia como
    "" faria esses 89% parecerem preenchidos com vazio."""
    esparso = parser.parse_processos(html)[1]
    assert esparso.risco is None
    assert esparso.fase_atual is None
    assert esparso.resultado is None
    assert esparso.contrario is None
    assert esparso.comarca is None


def test_tag_de_obra_vira_codigo(html: str) -> None:
    com_obra, sem_obra, _ = parser.parse_processos(html)
    assert com_obra.codigo_obra == "231"
    assert sem_obra.codigo_obra is None


def test_total_declarado_pelo_easyjur(html: str) -> None:
    """O pull compara o que coletou com isto, para nao aceitar em
    silencio uma coleta menor do que a base."""
    assert parser.total_registros(html) == 453


def test_pagina_sem_acao_listagem_e_reconhecida(html: str) -> None:
    """Sem `acao_listagem=enviar` o EasyJur devolve 200 com tabela
    vazia e "0 Registros Encontrados" -- foi assim que 453 processos
    viraram "0" em 17/09/2026."""
    vazio = (
        '<table id="processos_lista"><tbody></tbody></table>'
        '<div class="table-footer"><h3>0  Registros Encontrados</h3></div>'
    )
    assert parser.parse_processos(vazio) == []
    assert parser.total_registros(vazio) == 0


# --- codigo de obra ---------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "esperado"),
    [
        ("Januária - z231", "231"),
        ("Manhumirim - Z228", "228"),
        ("Abaete - z246", "246"),
        ("Equipe Exemplo", None),
        ("", None),
    ],
)
def test_codigo_de_obra(tag: str, esperado: str | None) -> None:
    """`z` e marcador descartavel no EasyJur. Validado em 19/09/2026:
    os 5 codigos da base batem com os locais do Tangerino, cidade
    inclusive."""
    assert parser.codigo_obra_de_grupo(tag) == esperado


# --- andamentos (CSV) -------------------------------------------------------


def test_csv_le_por_posicao_e_nao_pelo_cabecalho(csv_bytes: bytes) -> None:
    """O cabecalho do export esta ERRADO: 30 rotulos para 29 valores, e
    o numero do processo sai sob "Tipo de Acao". Ler pelo nome da coluna
    trocaria cliente por contrario numa acao trabalhista."""
    a = parser.parse_andamentos_csv(csv_bytes)[0]
    assert a.easyjur_id == 900000001
    assert a.numero_cnj == "0000001-01.2025.5.03.0001"
    assert a.descricao == "Distribuido por sorteio"
    assert a.data == date(2025, 6, 3)
    assert a.status == "PENDENTE"
    assert a.tipo == "Andamento do Tribunal"


def test_csv_tres_andamentos(csv_bytes: bytes) -> None:
    assert len(parser.parse_andamentos_csv(csv_bytes)) == 3


def test_csv_consertado_falha_alto(csv_bytes: bytes) -> None:
    """Se o EasyJur consertar o export (30 rotulos, 30 valores), o mapa
    posicional deixa de valer. Tem de FALHAR, nao reinterpretar tudo
    deslocado em silencio."""
    linhas = csv_bytes.decode("latin-1").split("\r\n")
    consertado = "\r\n".join(
        [linhas[0]] + [ln.replace('"900', '"X";"900', 1) for ln in linhas[1:] if ln]
    )
    with pytest.raises(parser.FormatoInesperadoError, match="29"):
        parser.parse_andamentos_csv(consertado.encode("latin-1"))


def test_csv_com_cnj_fora_da_posicao_8_falha(csv_bytes: bytes) -> None:
    trocado = csv_bytes.replace(b"0000001-01.2025.5.03.0001", b"nao-e-cnj")
    with pytest.raises(parser.FormatoInesperadoError, match="CNJ"):
        parser.parse_andamentos_csv(trocado)


def test_csv_so_com_cabecalho_e_sessao_sem_filtro() -> None:
    """Export chamado sem `pesquisa=enviar` antes devolve so o
    cabecalho. NAO e "base sem andamentos" -- e sessao sem filtro."""
    so_cab = (FIX / "andamentos_so_cabecalho.csv").read_bytes()
    with pytest.raises(parser.SessaoSemFiltroError):
        parser.parse_andamentos_csv(so_cab)


def test_acento_corrompido_e_preservado_como_veio(csv_bytes: bytes) -> None:
    """O export troca acento por "?" ("Peti??o"). Nao adivinhamos a
    letra: texto adivinhado tem cara de certo e pode estar errado."""
    a = parser.parse_andamentos_csv(csv_bytes)[2]
    assert a.descricao == "Juntada de Peti??o"
