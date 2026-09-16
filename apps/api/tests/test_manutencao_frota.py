

# --- regras de manutencao preventiva do cliente (Bruno, 11/09/2026) ---
#
# "Por ex caminhoes e carro 3000km / Maquinas 50 hrs".
# Antes daqui o codigo usava 250h (padrao de OEM) e NAO tinha gatilho
# por km -- caminhao e carro nunca geravam alerta.


def _parte(**kw):
    from app.modules.manutencao_frota.models import ParteDiaria

    base = dict(id=1, veiculo_id=1, ocr_status="revisado")
    base.update(kw)
    return ParteDiaria(**base)


def test_alerta_preventivo_maquina_a_cada_50_horas():
    from decimal import Decimal

    from app.modules.manutencao_frota.service import (
        calcular_consumo_parte_diaria,
    )

    # 49h -> 51h cruza o marco de 50h
    r = calcular_consumo_parte_diaria(
        _parte(horimetro_inicio=Decimal("49"), horimetro_fim=Decimal("51")),
        horimetro_anterior=Decimal("49"),
    )
    assert r["alerta_manutencao_preventiva"] is True

    # 51h -> 53h nao cruza marco nenhum
    r = calcular_consumo_parte_diaria(
        _parte(horimetro_inicio=Decimal("51"), horimetro_fim=Decimal("53")),
        horimetro_anterior=Decimal("51"),
    )
    assert r["alerta_manutencao_preventiva"] is False


def test_alerta_preventivo_veiculo_a_cada_3000_km():
    """O caso que NAO existia: caminhao e carro medem km, nao horas."""
    from app.modules.manutencao_frota.service import (
        calcular_consumo_parte_diaria,
    )

    r = calcular_consumo_parte_diaria(
        _parte(km_inicio=2950, km_fim=3010),
        km_anterior=2950,
    )
    assert r["alerta_manutencao_preventiva"] is True

    r = calcular_consumo_parte_diaria(
        _parte(km_inicio=3010, km_fim=3100),
        km_anterior=3010,
    )
    assert r["alerta_manutencao_preventiva"] is False


def test_gatilho_de_50h_nao_e_o_antigo_de_250h():
    """Trava a regra do cliente contra o padrao de OEM que estava aqui."""
    from decimal import Decimal

    from app.modules.manutencao_frota.service import (
        MANUTENCAO_PREVENTIVA_HORAS_INTERVALO,
        MANUTENCAO_PREVENTIVA_KM_INTERVALO,
    )

    assert Decimal("50") == MANUTENCAO_PREVENTIVA_HORAS_INTERVALO
    assert MANUTENCAO_PREVENTIVA_KM_INTERVALO == 3000
