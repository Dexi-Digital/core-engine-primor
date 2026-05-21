"""Seed de demo: cadastros extraidos do dossie de processos da PRIMOR.

Popula o banco com os 14 administrativos do organograma (Bruno, Evandro,
Brenda, Brendon, Gigante, Emilly, Paulo, Wanderson, Lorena, Samuel,
Marcelo, Dani, Rodrigo, Lorrayne) com cargos e setores reais, 4 empresas
do grupo (ZAG, Guaxima, CTC, CIRRUS) com certidoes e documentos
societarios, 2 saved queries do PNCP que espelham as rotinas de
licitacao da Brenda/Evandro, e 6 editais de exemplo dos orgaos onde a
empresa atua (DER/MG, DNIT, Prefeitura BH, AGETOP, DER/ES).

Overlay de alertas (para demo do card `Em alerta` em `/rh` e badges de
ASO em `/rh/funcionarios`): 1 admin com `status=afastado` (INSS) e 2
admins com ASO `vencendo` (<= 30 dias). Os demais ficam `ativo` com ASO
`vigente` (validade longa). A aplicacao do overlay e idempotente -- os
campos sao normalizados toda vez que o script roda.

CNPJs das empresas do grupo sao placeholders (`33000001000101` ... 4).
Quando o cliente informar os reais, basta substituir no script (ou rodar
um UPDATE direto).

Uso:
    cd apps/api
    uv run python -m scripts.seed_dossie

Idempotente: pode rodar quantas vezes quiser. Insercoes usam ON CONFLICT
DO NOTHING ou checagem previa.

NAO deleta nada. Para "resetar" mock antigo, faca por SQL manual ou pelo
proprio CRUD da aplicacao.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select, text

# Importa todos os modulos com modelos -- a metadata do SQLAlchemy precisa
# resolver FKs entre tabelas de modulos diferentes (ex: empresa_documentos
# referencia onedrive_sync_runs). Sem isso, qualquer commit aborta com
# `NoReferencedTableError`. Importar a app inteira mantem a ordem certa.
import app.modules.auth.models  # noqa: F401
import app.modules.diagnostico.models  # noqa: F401
import app.modules.dp_sesmt.models  # noqa: F401
import app.modules.fiscal.models  # noqa: F401
import app.modules.licitacoes.models  # noqa: F401
import app.modules.manutencao_frota.models  # noqa: F401
import app.modules.obras.models  # noqa: F401
import app.modules.onedrive_sync.models  # noqa: F401
from app.core.db import SessionLocal
from app.modules.dp_sesmt.models import (
    STATUS_AFASTADO,
    STATUS_ATIVO,
    Employee,
)
from app.modules.licitacoes.models import (
    CertidaoEmpresa,
    EmpresaDocumento,
    Licitacao,
    SavedQuery,
)
from app.modules.obras.models import Obra

logger = structlog.get_logger(__name__)


# CNPJs placeholders das 4 empresas do grupo PRIMOR (mantidos sem mascara
# pelo schema). Substitua pelos CNPJs reais quando disponiveis.
EMPRESA_ZAG = "33000001000101"
EMPRESA_GUAXIMA = "33000002000102"
EMPRESA_CTC = "33000003000103"
EMPRESA_CIRRUS = "33000004000104"


# --- 14 administrativos (organograma do dossie) ---------------------------
# CPFs sintaticamente validos (digitos verificadores corretos) -- nao sao
# CPFs reais, sao placeholders para a demo. Reais entram via UI.
#
# Overlay de demo: alguns admins recebem ASO/status especifico para
# exercitar o card `Em alerta` em /rh e os badges em /rh/funcionarios.
# Mantido como `today` resolvido em runtime para nao envelhecer.
_TODAY = date.today()
# 1 admin afastado -> Em alerta = 1 (filtro `status != ativo`).
# Marcelo (A-011) escolhido por ser nome generico (nao gerencial) e nao
# colidir com cargos sensiveis do organograma.
_AFASTADO_CPF = "00000011126"  # Marcelo
# 2 admins com ASO vencendo (<= 30 dias). Brendon e Paulo cobrem 2 setores
# distintos (Licitacoes + Planejamento), o que torna a listagem mais
# realista quando alguem filtra por setor.
_ASO_VENCENDO_CPFS = {
    "00000010405": 10,  # Brendon -> vence em ~10 dias
    "00000010740": 25,  # Paulo   -> vence em ~25 dias
}
_ADMINS: tuple[dict, ...] = (
    {
        "cpf": "00000010154",
        "nome": "Bruno Zago",
        "cargo": "Gestor de Planejamento e Controle",
        "matricula": "A-001",
        "setor": "Planejamento",
        "email": "bruno@primor.com.br",
        "admissao": date(2018, 3, 1),
    },
    {
        "cpf": "00000010235",
        "nome": "Evandro",
        "cargo": "Coordenador de Licitacoes",
        "matricula": "A-002",
        "setor": "Licitacoes",
        "email": "evandro@primor.com.br",
        "admissao": date(2019, 5, 22),
    },
    {
        "cpf": "00000010316",
        "nome": "Brenda",
        "cargo": "Analista de Licitacoes e Empresas",
        "matricula": "A-003",
        "setor": "Licitacoes",
        "email": "brenda@primor.com.br",
        "admissao": date(2020, 8, 10),
    },
    {
        "cpf": "00000010405",
        "nome": "Brendon",
        "cargo": "Analista Tecnico (Habilitacao/CREA)",
        "matricula": "A-004",
        "setor": "Licitacoes",
        "email": "brendon@primor.com.br",
        "admissao": date(2021, 2, 15),
    },
    {
        "cpf": "00000010588",
        "nome": "Gigante",
        "cargo": "Gerente de Orcamento e Adesoes",
        "matricula": "A-005",
        "setor": "Orcamento",
        "email": "gigante@primor.com.br",
        "admissao": date(2017, 11, 3),
    },
    {
        "cpf": "00000010669",
        "nome": "Emilly",
        "cargo": "Analista de Contratos",
        "matricula": "A-006",
        "setor": "Contratos",
        "email": "emilly@primor.com.br",
        "admissao": date(2020, 4, 18),
    },
    {
        "cpf": "00000010740",
        "nome": "Paulo",
        "cargo": "Analista de Planejamento e TI",
        "matricula": "A-007",
        "setor": "Planejamento",
        "email": "paulo@primor.com.br",
        "admissao": date(2019, 9, 12),
    },
    {
        "cpf": "00000010820",
        "nome": "Wanderson",
        "cargo": "Coordenador de TI",
        "matricula": "A-008",
        "setor": "TI",
        "email": "wanderson@primor.com.br",
        "admissao": date(2018, 7, 1),
    },
    {
        "cpf": "00000010901",
        "nome": "Lorena",
        "cargo": "Assistente Administrativo TI",
        "matricula": "A-009",
        "setor": "TI",
        "email": "lorena@primor.com.br",
        "admissao": date(2022, 1, 10),
    },
    {
        "cpf": "00000011045",
        "nome": "Samuel",
        "cargo": "Coordenador Comercial",
        "matricula": "A-010",
        "setor": "Comercial",
        "email": "samuel@primor.com.br",
        "admissao": date(2019, 3, 5),
    },
    {
        "cpf": "00000011126",
        "nome": "Marcelo",
        "cargo": "Analista Comercial",
        "matricula": "A-011",
        "setor": "Comercial",
        "email": "marcelo@primor.com.br",
        "admissao": date(2021, 6, 20),
    },
    {
        "cpf": "00000011207",
        "nome": "Dani",
        "cargo": "Analista Administrativo",
        "matricula": "A-012",
        "setor": "Administrativo",
        "email": "dani@primor.com.br",
        "admissao": date(2020, 10, 1),
    },
    {
        "cpf": "00000011398",
        "nome": "Rodrigo Zago",
        "cargo": "Diretor Tecnico",
        "matricula": "A-013",
        "setor": "Diretoria",
        "email": "rodrigo@primor.com.br",
        "admissao": date(2015, 1, 5),
    },
    {
        "cpf": "00000011479",
        "nome": "Lorrayne Paraiso",
        "cargo": "Consultora Externa",
        "matricula": "A-014",
        "setor": "Consultoria",
        "email": "lorrayne@dexidigital.com.br",
        "admissao": date(2025, 9, 1),
    },
)


_OBRAS: tuple[dict, ...] = (
    {
        "codigo": "z209",
        "nome": "Restauracao MG-010 trecho km 18 a km 47",
        "cliente": "DER/MG - Departamento de Estradas de Rodagem",
        "uf": "MG",
        "cidade": "Lagoa Santa",
        "data_inicio": date(2025, 8, 15),
    },
    {
        "codigo": "z210",
        "nome": "Pavimentacao Av. Cristiano Machado - lote 3",
        "cliente": "Prefeitura Municipal de Belo Horizonte",
        "uf": "MG",
        "cidade": "Belo Horizonte",
        "data_inicio": date(2025, 10, 1),
    },
    {
        "codigo": "z211",
        "nome": "Recapeamento BR-040 km 562 a km 580",
        "cliente": "DNIT - Departamento Nacional de Infraestrutura",
        "uf": "MG",
        "cidade": "Sete Lagoas",
        "data_inicio": date(2026, 1, 12),
    },
    {
        "codigo": "z212",
        "nome": "Adesao Ata SRP - Tapa-buracos Regional Norte",
        "cliente": "Prefeitura de Belo Horizonte",
        "uf": "MG",
        "cidade": "Belo Horizonte",
        "data_inicio": date(2026, 3, 1),
    },
    {
        "codigo": "z213",
        "nome": "Restauracao GO-070 trecho industrial",
        "cliente": "AGETOP",
        "uf": "GO",
        "cidade": "Goiania",
        "data_inicio": date(2026, 4, 1),
    },
)


_CERTIDOES: tuple[dict, ...] = (
    # ZAG (responsavel tecnico proprio)
    {
        "cnpj": EMPRESA_ZAG,
        "tipo": "cnd_federal",
        "numero": "0001-2026/ZAG",
        "emissao": date(2026, 3, 15),
        "validade": date(2026, 9, 15),
        "orgao": "Receita Federal",
        "obs": "ZAG Engenharia",
    },
    {
        "cnpj": EMPRESA_ZAG,
        "tipo": "cnd_fgts",
        "numero": "0002-2026/ZAG",
        "emissao": date(2026, 4, 1),
        "validade": date(2026, 6, 30),
        "orgao": "Caixa Economica Federal",
        "obs": "ZAG Engenharia",
    },
    {
        "cnpj": EMPRESA_ZAG,
        "tipo": "cnd_trabalhista",
        "numero": "0003-2026/ZAG",
        "emissao": date(2026, 2, 10),
        "validade": date(2026, 8, 10),
        "orgao": "TST",
        "obs": "ZAG Engenharia",
    },
    {
        "cnpj": EMPRESA_ZAG,
        "tipo": "cat_capacidade_tecnica",
        "numero": "CAT-1234/2025",
        "emissao": date(2025, 11, 20),
        "validade": date(2027, 11, 20),
        "orgao": "CREA/MG",
        "obs": "ZAG - atestado BR-262",
    },
    # Guaxima (responsavel tecnico proprio)
    {
        "cnpj": EMPRESA_GUAXIMA,
        "tipo": "cnd_federal",
        "numero": "0001-2026/GUAX",
        "emissao": date(2026, 3, 5),
        "validade": date(2026, 9, 5),
        "orgao": "Receita Federal",
        "obs": "Guaxima Engenharia",
    },
    {
        "cnpj": EMPRESA_GUAXIMA,
        "tipo": "cnd_fgts",
        "numero": "0002-2026/GUAX",
        "emissao": date(2026, 4, 5),
        "validade": date(2026, 7, 3),
        "orgao": "Caixa Economica Federal",
        "obs": "Guaxima Engenharia",
    },
    {
        "cnpj": EMPRESA_GUAXIMA,
        "tipo": "cnd_trabalhista",
        "numero": "0003-2026/GUAX",
        "emissao": date(2026, 1, 22),
        "validade": date(2026, 7, 22),
        "orgao": "TST",
        "obs": "Guaxima Engenharia",
    },
    {
        "cnpj": EMPRESA_GUAXIMA,
        "tipo": "cat_capacidade_tecnica",
        "numero": "CAT-987/2024",
        "emissao": date(2024, 6, 18),
        "validade": date(2026, 6, 18),
        "orgao": "CREA/MG",
        "obs": "Guaxima - atestado MG-050",
    },
    # CTC (sem responsavel tecnico interno)
    {
        "cnpj": EMPRESA_CTC,
        "tipo": "cnd_federal",
        "numero": "0001-2026/CTC",
        "emissao": date(2026, 4, 10),
        "validade": date(2026, 10, 10),
        "orgao": "Receita Federal",
        "obs": "CTC Construcoes - SEM RT interno",
    },
    {
        "cnpj": EMPRESA_CTC,
        "tipo": "cnd_fgts",
        "numero": "0002-2026/CTC",
        "emissao": date(2026, 4, 12),
        "validade": date(2026, 7, 10),
        "orgao": "Caixa Economica Federal",
        "obs": "CTC Construcoes",
    },
    {
        "cnpj": EMPRESA_CTC,
        "tipo": "cnd_trabalhista",
        "numero": "0003-2026/CTC",
        "emissao": date(2026, 3, 8),
        "validade": date(2026, 9, 8),
        "orgao": "TST",
        "obs": "CTC Construcoes",
    },
    # CIRRUS (sem RT) -- exemplo com vencida + vencendo (alertas devem disparar)
    {
        "cnpj": EMPRESA_CIRRUS,
        "tipo": "cnd_federal",
        "numero": "0001-2026/CIR",
        "emissao": date(2026, 4, 18),
        "validade": date(2026, 5, 18),  # vencendo em ~30d
        "orgao": "Receita Federal",
        "obs": "CIRRUS Engenharia - SEM RT interno - VENCENDO",
    },
    {
        "cnpj": EMPRESA_CIRRUS,
        "tipo": "cnd_fgts",
        "numero": "0002-2026/CIR",
        "emissao": date(2026, 4, 8),
        "validade": date(2026, 7, 7),
        "orgao": "Caixa Economica Federal",
        "obs": "CIRRUS Engenharia",
    },
    {
        "cnpj": EMPRESA_CIRRUS,
        "tipo": "cnd_trabalhista",
        "numero": "0003-2026/CIR",
        "emissao": date(2025, 10, 10),
        "validade": date(2026, 4, 10),  # vencida
        "orgao": "TST",
        "obs": "CIRRUS Engenharia - VENCIDA",
    },
)


_DOCS_EMPRESA: tuple[dict, ...] = (
    {
        "cnpj": EMPRESA_ZAG,
        "tipo": "CONTRATO_SOCIAL",
        "numero": "ZAG-CS-2018",
        "emissao": date(2018, 3, 1),
        "orgao": "JUCEMG",
        "obs": "ZAG - Contrato social consolidado",
    },
    {
        "cnpj": EMPRESA_ZAG,
        "tipo": "BALANCO_PATRIMONIAL",
        "numero": "ZAG-BAL-2024",
        "emissao": date(2025, 3, 30),
        "orgao": "Contabilidade interna",
        "obs": "ZAG - Balanco exercicio 2024",
    },
    {
        "cnpj": EMPRESA_ZAG,
        "tipo": "CAGEF",
        "numero": "CAGEF-ZAG-2025",
        "emissao": date(2025, 7, 12),
        "validade": date(2027, 7, 12),
        "orgao": "CAGEF/MG",
        "obs": "ZAG - cadastro estadual MG vigente",
    },
    {
        "cnpj": EMPRESA_ZAG,
        "tipo": "SICAF",
        "numero": "SICAF-ZAG-2025",
        "emissao": date(2025, 9, 1),
        "validade": date(2026, 9, 1),
        "orgao": "Compras.gov.br",
        "obs": "ZAG - cadastro federal",
    },
    {
        "cnpj": EMPRESA_GUAXIMA,
        "tipo": "CONTRATO_SOCIAL",
        "numero": "GUAX-CS-2019",
        "emissao": date(2019, 5, 15),
        "orgao": "JUCEMG",
        "obs": "Guaxima - Contrato social",
    },
    {
        "cnpj": EMPRESA_GUAXIMA,
        "tipo": "BALANCO_PATRIMONIAL",
        "numero": "GUAX-BAL-2024",
        "emissao": date(2025, 4, 10),
        "orgao": "Contabilidade interna",
        "obs": "Guaxima - Balanco 2024",
    },
    {
        "cnpj": EMPRESA_GUAXIMA,
        "tipo": "CAGEF",
        "numero": "CAGEF-GUAX-2025",
        "emissao": date(2025, 8, 1),
        "validade": date(2027, 8, 1),
        "orgao": "CAGEF/MG",
        "obs": "Guaxima - cadastro estadual MG",
    },
    {
        "cnpj": EMPRESA_CTC,
        "tipo": "CONTRATO_SOCIAL",
        "numero": "CTC-CS-2021",
        "emissao": date(2021, 2, 8),
        "orgao": "JUCEMG",
        "obs": "CTC - Contrato social",
    },
    {
        "cnpj": EMPRESA_CTC,
        "tipo": "BALANCO_PATRIMONIAL",
        "numero": "CTC-BAL-2024",
        "emissao": date(2025, 5, 20),
        "orgao": "Contabilidade interna",
        "obs": "CTC - Balanco 2024",
    },
    {
        "cnpj": EMPRESA_CIRRUS,
        "tipo": "CONTRATO_SOCIAL",
        "numero": "CIR-CS-2022",
        "emissao": date(2022, 11, 30),
        "orgao": "JUCEMG",
        "obs": "CIRRUS - Contrato social",
    },
    {
        "cnpj": EMPRESA_CIRRUS,
        "tipo": "BALANCO_PATRIMONIAL",
        "numero": "CIR-BAL-2024",
        "emissao": date(2025, 4, 25),
        "orgao": "Contabilidade interna",
        "obs": "CIRRUS - Balanco 2024",
    },
)


_SAVED_QUERIES: tuple[dict, ...] = (
    {
        "nome": "Infraestrutura viaria MG/ES/GO/MT/RO (Evandro)",
        "user_email": "evandro@primor.com.br",
        "recipients": ["evandro@primor.com.br", "brenda@primor.com.br"],
        "uf": "MG",
        "modalidade": None,
        "search": "pavimentacao restauracao recapeamento rodovia",
        "orgao_cnpj": None,
    },
    {
        "nome": "Adesoes SRP - Atas em MG (Brenda)",
        "user_email": "brenda@primor.com.br",
        "recipients": ["brenda@primor.com.br", "gigante@primor.com.br"],
        "uf": "MG",
        "modalidade": "Pregao Eletronico",
        "search": "SRP ata registro precos",
        "orgao_cnpj": None,
    },
)


_LICITACOES: tuple[dict, ...] = (
    {
        "external_id": "demo-dermg-001",
        "source": "pncp",
        "numero_compra": "00001/2026",
        "ano_compra": 2026,
        "sequencial_compra": 1,
        "objeto_compra": (
            "Restauracao do pavimento da MG-010 - trecho km 18 a km 47"
        ),
        "modalidade_nome": "Concorrencia",
        "modo_disputa_nome": "Aberto",
        "situacao_compra_nome": "Publicada",
        "valor_total_estimado": Decimal("42500000.00"),
        "srp": False,
        "orgao_cnpj": "17309564000160",
        "orgao_razao_social": (
            "DER/MG - Departamento de Estradas de Rodagem"
        ),
        "uf_sigla": "MG",
        "municipio_nome": "Belo Horizonte",
        "data_publicacao_pncp": date(2026, 4, 12),
    },
    {
        "external_id": "demo-dnit-001",
        "source": "pncp",
        "numero_compra": "00120/2026",
        "ano_compra": 2026,
        "sequencial_compra": 120,
        "objeto_compra": (
            "Servicos de conservacao rodoviaria BR-040 trecho MG"
        ),
        "modalidade_nome": "Concorrencia",
        "modo_disputa_nome": "Aberto",
        "situacao_compra_nome": "Publicada",
        "valor_total_estimado": Decimal("28900000.00"),
        "srp": False,
        "orgao_cnpj": "04892707000100",
        "orgao_razao_social": "DNIT - Superintendencia Regional MG",
        "uf_sigla": "MG",
        "municipio_nome": "Belo Horizonte",
        "data_publicacao_pncp": date(2026, 4, 18),
    },
    {
        "external_id": "demo-pbh-001",
        "source": "pncp",
        "numero_compra": "00345/2026",
        "ano_compra": 2026,
        "sequencial_compra": 345,
        "objeto_compra": (
            "Pavimentacao asfaltica - Av. Cristiano Machado - Lote 3"
        ),
        "modalidade_nome": "Concorrencia",
        "modo_disputa_nome": "Aberto",
        "situacao_compra_nome": "Publicada",
        "valor_total_estimado": Decimal("12300000.00"),
        "srp": False,
        "orgao_cnpj": "18715383000140",
        "orgao_razao_social": (
            "Prefeitura Municipal de Belo Horizonte - SUDECAP"
        ),
        "uf_sigla": "MG",
        "municipio_nome": "Belo Horizonte",
        "data_publicacao_pncp": date(2026, 4, 20),
    },
    {
        "external_id": "demo-pbh-002",
        "source": "pncp",
        "numero_compra": "00346/2026",
        "ano_compra": 2026,
        "sequencial_compra": 346,
        "objeto_compra": (
            "SRP - Servicos de tapa-buracos e operacao "
            "tapa-buracos - Regional Norte"
        ),
        "modalidade_nome": "Pregao Eletronico",
        "modo_disputa_nome": "Aberto",
        "situacao_compra_nome": "Publicada",
        "valor_total_estimado": Decimal("8500000.00"),
        "srp": True,
        "orgao_cnpj": "18715383000140",
        "orgao_razao_social": (
            "Prefeitura Municipal de Belo Horizonte - SUDECAP"
        ),
        "uf_sigla": "MG",
        "municipio_nome": "Belo Horizonte",
        "data_publicacao_pncp": date(2026, 4, 22),
    },
    {
        "external_id": "demo-agetop-001",
        "source": "pncp",
        "numero_compra": "00078/2026",
        "ano_compra": 2026,
        "sequencial_compra": 78,
        "objeto_compra": "Restauracao GO-070 - trecho industrial",
        "modalidade_nome": "Concorrencia",
        "modo_disputa_nome": "Aberto",
        "situacao_compra_nome": "Publicada",
        "valor_total_estimado": Decimal("19800000.00"),
        "srp": False,
        "orgao_cnpj": "02488114000169",
        "orgao_razao_social": (
            "AGETOP - Agencia Goiana de Infraestrutura e Transportes"
        ),
        "uf_sigla": "GO",
        "municipio_nome": "Goiania",
        "data_publicacao_pncp": date(2026, 4, 15),
    },
    {
        "external_id": "demo-der-es-001",
        "source": "pncp",
        "numero_compra": "00042/2026",
        "ano_compra": 2026,
        "sequencial_compra": 42,
        "objeto_compra": "Pavimentacao ES-060 - acesso litoral norte",
        "modalidade_nome": "Concorrencia",
        "modo_disputa_nome": "Aberto",
        "situacao_compra_nome": "Publicada",
        "valor_total_estimado": Decimal("15600000.00"),
        "srp": False,
        "orgao_cnpj": "28161362000183",
        "orgao_razao_social": (
            "DER/ES - Departamento de Edificacoes e Rodovias"
        ),
        "uf_sigla": "ES",
        "municipio_nome": "Vitoria",
        "data_publicacao_pncp": date(2026, 4, 17),
    },
)


def _overlay_for(cpf: str) -> dict:
    """Calcula os campos `status` + ASO de demo para um admin.

    Default: ativo, ASO vigente (today + 180d). Excecoes:
    - `_AFASTADO_CPF`           -> status=afastado, ASO vigente.
    - `_ASO_VENCENDO_CPFS[cpf]` -> status=ativo, ASO vencendo (today + Nd).
    """
    if cpf == _AFASTADO_CPF:
        return {
            "status": STATUS_AFASTADO,
            "aso_data": _TODAY - timedelta(days=180),
            "aso_validade": _TODAY + timedelta(days=180),
            "aso_resultado": "apto",
        }
    if cpf in _ASO_VENCENDO_CPFS:
        dias = _ASO_VENCENDO_CPFS[cpf]
        return {
            "status": STATUS_ATIVO,
            # ASO vale 1 ano -- exame ha ~(365-dias) atras.
            "aso_data": _TODAY - timedelta(days=365 - dias),
            "aso_validade": _TODAY + timedelta(days=dias),
            "aso_resultado": "apto",
        }
    return {
        "status": STATUS_ATIVO,
        "aso_data": _TODAY - timedelta(days=180),
        "aso_validade": _TODAY + timedelta(days=180),
        "aso_resultado": "apto",
    }


async def _upsert_admins() -> int:
    """Insere os 14 administrativos. Idempotente via CPF unico.

    `is_admin_office=True` flag-os para o checklist do diagnostico documental
    (aliviar NR-18 etc) e tambem permite filtrar como "equipe administrativa"
    nas telas de RH.

    Tambem aplica o overlay de demo (status afastado / ASO vencendo) em
    todas as execucoes -- inclusive em admins que ja existiam -- para que
    a demo do card `Em alerta` e dos badges fique sempre consistente.
    """
    created = 0
    async with SessionLocal() as db:
        for entry in _ADMINS:
            overlay = _overlay_for(entry["cpf"])
            existing = (
                await db.execute(
                    select(Employee).where(Employee.cpf == entry["cpf"])
                )
            ).scalar_one_or_none()
            if existing is not None:
                # Re-sync do overlay -- o /rh card depende destes valores
                # estarem corretos toda vez que a demo rodar.
                existing.status = overlay["status"]
                existing.aso_data = overlay["aso_data"]
                existing.aso_validade = overlay["aso_validade"]
                existing.aso_resultado = overlay["aso_resultado"]
                continue
            emp = Employee(
                cpf=entry["cpf"],
                nome_completo=entry["nome"],
                cargo=entry["cargo"],
                matricula=entry["matricula"],
                setor=entry["setor"],
                obra=None,
                tipo_contrato="CLT",
                data_admissao=entry["admissao"],
                email=entry["email"],
                uf="MG",
                is_admin_office=True,
                source="seed_dossie",
                status=overlay["status"],
                aso_data=overlay["aso_data"],
                aso_validade=overlay["aso_validade"],
                aso_resultado=overlay["aso_resultado"],
            )
            db.add(emp)
            created += 1
        await db.commit()
    return created


async def _upsert_obras() -> int:
    created = 0
    async with SessionLocal() as db:
        for entry in _OBRAS:
            existing = (
                await db.execute(
                    select(Obra).where(Obra.codigo == entry["codigo"])
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            obra = Obra(
                codigo=entry["codigo"],
                nome=entry["nome"],
                cliente=entry["cliente"],
                uf=entry["uf"],
                cidade=entry["cidade"],
                status="ativa",
                data_inicio=entry["data_inicio"],
            )
            db.add(obra)
            created += 1
        await db.commit()
    return created


async def _upsert_certidoes() -> int:
    created = 0
    async with SessionLocal() as db:
        for entry in _CERTIDOES:
            existing = (
                await db.execute(
                    select(CertidaoEmpresa).where(
                        CertidaoEmpresa.empresa_cnpj == entry["cnpj"],
                        CertidaoEmpresa.tipo == entry["tipo"],
                        CertidaoEmpresa.numero == entry["numero"],
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            cert = CertidaoEmpresa(
                empresa_cnpj=entry["cnpj"],
                tipo=entry["tipo"],
                numero=entry["numero"],
                emissao=entry["emissao"],
                validade=entry["validade"],
                orgao_emissor=entry["orgao"],
                observacoes=entry["obs"],
            )
            db.add(cert)
            created += 1
        await db.commit()
    return created


async def _upsert_documentos_empresa() -> int:
    created = 0
    async with SessionLocal() as db:
        for entry in _DOCS_EMPRESA:
            existing = (
                await db.execute(
                    select(EmpresaDocumento).where(
                        EmpresaDocumento.empresa_cnpj == entry["cnpj"],
                        EmpresaDocumento.tipo == entry["tipo"],
                        EmpresaDocumento.numero == entry["numero"],
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            doc = EmpresaDocumento(
                empresa_cnpj=entry["cnpj"],
                tipo=entry["tipo"],
                numero=entry["numero"],
                emissao=entry["emissao"],
                validade=entry.get("validade"),
                orgao_emissor=entry["orgao"],
                observacoes=entry["obs"],
            )
            db.add(doc)
            created += 1
        await db.commit()
    return created


async def _upsert_saved_queries() -> int:
    created = 0
    async with SessionLocal() as db:
        for entry in _SAVED_QUERIES:
            existing = (
                await db.execute(
                    select(SavedQuery).where(SavedQuery.nome == entry["nome"])
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            sq = SavedQuery(
                nome=entry["nome"],
                user_email=entry["user_email"],
                recipients=entry["recipients"],
                uf=entry["uf"],
                modalidade=entry["modalidade"],
                search=entry["search"],
                orgao_cnpj=entry["orgao_cnpj"],
                active=True,
            )
            db.add(sq)
            created += 1
        await db.commit()
    return created


async def _upsert_licitacoes() -> int:
    created = 0
    async with SessionLocal() as db:
        for entry in _LICITACOES:
            existing = (
                await db.execute(
                    select(Licitacao).where(
                        Licitacao.external_id == entry["external_id"]
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            from datetime import datetime

            published_at = entry["data_publicacao_pncp"]
            if isinstance(published_at, date):
                published_at = datetime(
                    published_at.year,
                    published_at.month,
                    published_at.day,
                    10,
                    0,
                    0,
                    tzinfo=UTC,
                )
            lic = Licitacao(
                external_id=entry["external_id"],
                source=entry["source"],
                numero_compra=entry["numero_compra"],
                ano_compra=entry["ano_compra"],
                sequencial_compra=entry["sequencial_compra"],
                objeto_compra=entry["objeto_compra"],
                modalidade_nome=entry["modalidade_nome"],
                modo_disputa_nome=entry["modo_disputa_nome"],
                situacao_compra_nome=entry["situacao_compra_nome"],
                valor_total_estimado=entry["valor_total_estimado"],
                srp=entry["srp"],
                orgao_cnpj=entry["orgao_cnpj"],
                orgao_razao_social=entry["orgao_razao_social"],
                uf_sigla=entry["uf_sigla"],
                municipio_nome=entry["municipio_nome"],
                data_publicacao_pncp=published_at,
                raw=None,
            )
            db.add(lic)
            created += 1
        await db.commit()
    return created


async def main() -> None:
    logger.info("seed_dossie: starting")

    # Touch o banco -- da pra rodar dentro de docker compose ou local.
    async with SessionLocal() as db:
        await db.execute(text("select 1"))

    counts = {
        "admins": await _upsert_admins(),
        "obras": await _upsert_obras(),
        "certidoes": await _upsert_certidoes(),
        "docs_empresa": await _upsert_documentos_empresa(),
        "saved_queries": await _upsert_saved_queries(),
        "licitacoes": await _upsert_licitacoes(),
    }
    logger.info("seed_dossie: done", **counts)
    total = sum(counts.values())
    print(
        "seed_dossie: criados "
        + ", ".join(f"{k}={v}" for k, v in counts.items())
        + f" (total={total})"
    )


if __name__ == "__main__":
    asyncio.run(main())
