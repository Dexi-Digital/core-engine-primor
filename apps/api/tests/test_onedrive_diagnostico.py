"""Tests pro diagnostico de pastas OneDrive.

Cobre os 5 tipos de finding:
  - FALTANDO / EXTRA / FORA_DO_PADRAO
  - ENTIDADE_FANTASMA / ENTIDADE_SEM_PASTA

Reusa `OneDriveMockClient` pra evitar dependencia de Graph real.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.onedrive.client import OneDriveMockClient
from app.modules.dp_sesmt.models import Employee
from app.modules.manutencao_frota.models import Veiculo
from app.modules.obras.models import Obra
from app.modules.onedrive_diagnostico.service import (
    FINDING_ENTIDADE_FANTASMA,
    FINDING_ENTIDADE_SEM_PASTA,
    FINDING_EXTRA,
    FINDING_FALTANDO,
    FINDING_FORA_DO_PADRAO,
    FINDING_OK,
    _classify_path,
    _ext_stem,
    run_diagnostico,
)
from app.modules.onedrive_diagnostico.spec import (
    AREA_DP,
    AREA_EMPRESA,
    AREA_FROTA,
    AREA_OBRAS,
    SPEC,
)

# -------------------- unit: helpers puros ---------------------------------


def test_ext_stem_basic() -> None:
    assert _ext_stem("CRLV.pdf") == "CRLV"
    assert _ext_stem("Contrato Social.pdf") == "CONTRATO_SOCIAL"
    assert _ext_stem("a.b.c.pdf") == "A.B.C"
    assert _ext_stem("noext") is None
    assert _ext_stem(".hidden") is None


def test_classify_path_dp() -> None:
    area, key, tipo, _ = _classify_path("dp/42/CTPS.pdf")
    assert area == "dp"
    assert key == "42"
    assert tipo == "CTPS"


def test_classify_path_empresa_singleton() -> None:
    area, key, tipo, _ = _classify_path("empresa/CONTRATO_SOCIAL.pdf")
    assert area == "empresa"
    assert key is None
    assert tipo == "CONTRATO_SOCIAL"


def test_classify_path_fora_do_padrao_area_invalida() -> None:
    area, _k, _t, reason = _classify_path("lixo/qualquer.pdf")
    assert area is None
    assert "area" in reason or "conhecida" in reason.lower()


def test_classify_path_fora_do_padrao_empresa_com_subpasta() -> None:
    area, _k, _t, reason = _classify_path("empresa/subpasta/arq.pdf")
    assert area is None
    assert "empresa" in reason


def test_classify_path_fora_do_padrao_dp_sem_entidade() -> None:
    area, _k, _t, reason = _classify_path("dp/arq.pdf")
    assert area is None


def test_spec_required_subsets_known() -> None:
    for area, spec in SPEC.items():
        assert spec.required.issubset(spec.known), f"{area} required leak"
        assert spec.required.isdisjoint(spec.optional), f"{area} dup"


# -------------------- integration: run_diagnostico ------------------------


async def _seed_entities(
    db: AsyncSession,
) -> tuple[Employee, Veiculo, Obra]:
    emp = Employee(
        cpf="111.111.111-11",
        nome_completo="Joao Operario",
        cargo="Pedreiro",
        status="ativo",
    )
    db.add(emp)
    veic = Veiculo(placa="ABC1D23", marca="Ford", modelo="Cargo", status="ativo")
    db.add(veic)
    obra = Obra(codigo="OBR-001", nome="Sede", status="ativa")
    db.add(obra)
    await db.commit()
    await db.refresh(emp)
    await db.refresh(veic)
    await db.refresh(obra)
    return emp, veic, obra


@pytest.mark.asyncio
async def test_diagnostico_ok_quando_tudo_minimo_presente(
    db_session: AsyncSession,
) -> None:
    emp, veic, obra = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    # DP: 3 required (CTPS, CONTRATO_TRABALHO, FICHA_REGISTRO)
    for t in ("CTPS", "CONTRATO_TRABALHO", "FICHA_REGISTRO"):
        client.seed(relative_path=f"dp/{emp.id}/{t}.pdf")
    # Frota: 3 required (crlv, seguro, ipva)
    for t in ("CRLV", "SEGURO", "IPVA"):
        client.seed(relative_path=f"frota/{veic.placa}/{t}.pdf")
    # Obras: ART + ALVARA
    for t in ("ART", "ALVARA"):
        client.seed(relative_path=f"obras/{obra.codigo}/{t}.pdf")
    # Empresa: CONTRATO_SOCIAL
    client.seed(relative_path="empresa/CONTRATO_SOCIAL.pdf")

    result = await run_diagnostico(db_session, client=client, area="all")

    assert result.total_files == 9
    assert result.counts[FINDING_FALTANDO] == 0
    assert result.counts[FINDING_EXTRA] == 0
    assert result.counts[FINDING_FORA_DO_PADRAO] == 0
    assert result.counts[FINDING_ENTIDADE_FANTASMA] == 0
    assert result.counts[FINDING_ENTIDADE_SEM_PASTA] == 0
    # todas as entidades reportam ok=True
    for area in (AREA_DP, AREA_FROTA, AREA_OBRAS, AREA_EMPRESA):
        reports = result.by_area[area]
        assert all(r.ok for r in reports), (
            f"esperava ok=True em {area}, got {reports}"
        )


@pytest.mark.asyncio
async def test_diagnostico_detecta_faltando(
    db_session: AsyncSession,
) -> None:
    emp, _v, _o = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    # DP: so CTPS -- faltam CONTRATO_TRABALHO e FICHA_REGISTRO
    client.seed(relative_path=f"dp/{emp.id}/CTPS.pdf")

    result = await run_diagnostico(db_session, client=client, area=AREA_DP)

    dp_reports = result.by_area[AREA_DP]
    emp_reports = [r for r in dp_reports if str(emp.id) in r.label or r.entity_key == str(emp.id)]
    assert emp_reports
    emp_report = emp_reports[0]
    faltando = [f for f in emp_report.findings if f.kind == FINDING_FALTANDO]
    tipos = {f.doc_tipo for f in faltando}
    assert tipos == {"CONTRATO_TRABALHO", "FICHA_REGISTRO"}
    assert not emp_report.ok


@pytest.mark.asyncio
async def test_diagnostico_detecta_extra(
    db_session: AsyncSession,
) -> None:
    emp, _v, _o = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    # DP: todos os required + 1 arquivo com tipo nao reconhecido
    for t in ("CTPS", "CONTRATO_TRABALHO", "FICHA_REGISTRO"):
        client.seed(relative_path=f"dp/{emp.id}/{t}.pdf")
    client.seed(relative_path=f"dp/{emp.id}/FOTO_PERFIL.jpg")

    result = await run_diagnostico(db_session, client=client, area=AREA_DP)
    # busca a report da entidade `emp`
    emp_reports = [
        r for r in result.by_area[AREA_DP] if r.entity_key == str(emp.id)
    ]
    assert emp_reports
    extras = [f for f in emp_reports[0].findings if f.kind == FINDING_EXTRA]
    assert len(extras) == 1
    assert extras[0].doc_tipo == "FOTO_PERFIL"


@pytest.mark.asyncio
async def test_diagnostico_detecta_fora_do_padrao(
    db_session: AsyncSession,
) -> None:
    _e, _v, _o = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path="lixo/arquivo.pdf")
    client.seed(relative_path="empresa/subpasta/nested.pdf")

    result = await run_diagnostico(db_session, client=client, area="all")

    assert result.counts[FINDING_FORA_DO_PADRAO] == 2


@pytest.mark.asyncio
async def test_diagnostico_detecta_entidade_fantasma(
    db_session: AsyncSession,
) -> None:
    await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    # employee_id 99999 nao existe no DB
    client.seed(relative_path="dp/99999/CTPS.pdf")
    # placa XYZ9Z99 nao existe
    client.seed(relative_path="frota/XYZ9Z99/CRLV.pdf")

    result = await run_diagnostico(db_session, client=client, area="all")
    assert result.counts[FINDING_ENTIDADE_FANTASMA] == 2


@pytest.mark.asyncio
async def test_diagnostico_detecta_entidade_sem_pasta(
    db_session: AsyncSession,
) -> None:
    """Entidade existe no DB mas nenhum arquivo na pasta do OneDrive."""
    await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    # 0 arquivos -> todas as entidades ficam sem pasta
    result = await run_diagnostico(db_session, client=client, area="all")
    assert result.counts[FINDING_ENTIDADE_SEM_PASTA] >= 4  # emp + veic + obra + empresa


@pytest.mark.asyncio
async def test_diagnostico_area_filter(
    db_session: AsyncSession,
) -> None:
    _e, _v, _o = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path="empresa/CONTRATO_SOCIAL.pdf")

    result = await run_diagnostico(db_session, client=client, area=AREA_EMPRESA)
    # so empresa no output
    assert set(result.by_area.keys()) == {AREA_EMPRESA}


@pytest.mark.asyncio
async def test_diagnostico_area_invalida_raise(
    db_session: AsyncSession,
) -> None:
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    with pytest.raises(ValueError):
        await run_diagnostico(db_session, client=client, area="invalida")


@pytest.mark.asyncio
async def test_diagnostico_conta_ok_quando_arquivo_valido(
    db_session: AsyncSession,
) -> None:
    emp, _v, _o = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path=f"dp/{emp.id}/CTPS.pdf")
    client.seed(relative_path=f"dp/{emp.id}/CONTRATO_TRABALHO.pdf")
    client.seed(relative_path=f"dp/{emp.id}/FICHA_REGISTRO.pdf")

    result = await run_diagnostico(db_session, client=client, area=AREA_DP)
    assert result.counts[FINDING_OK] == 3


@pytest.mark.asyncio
async def test_diagnostico_normaliza_placa_com_hifen(
    db_session: AsyncSession,
) -> None:
    _e, veic, _o = await _seed_entities(db_session)
    # placa salva no DB: ABC1D23. User sobe pasta como ABC-1D23 (hifen)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path="frota/ABC-1D23/CRLV.pdf")

    result = await run_diagnostico(db_session, client=client, area=AREA_FROTA)
    # nao deveria gerar fantasma (o hifen normaliza)
    assert result.counts[FINDING_ENTIDADE_FANTASMA] == 0
    # mas as pastas frota/ABC1D23 e frota/ABC-1D23 geram chaves diferentes
    # -> aceita que o diagnostico ache a entidade certa
    frota_reports = result.by_area[AREA_FROTA]
    matched = [r for r in frota_reports if "ABC1D23" in (r.label or "")]
    assert matched, f"esperava report pra placa ABC1D23, got {frota_reports}"
