"""D.6 fase 2 -- consulta CREA via Infosimples + import ART como certidao.

Fluxo principal:
  1. Operador chama `POST /api/v1/licitacoes/certidoes/consultar-crea`
     com {uf, tipo, identificador} -> service dispara
     `client.consultar_crea()`, persiste row em `crea_consultas`
     (audit + cache), retorna payload normalizado.
  2. Quando o tipo for "art" e a operadora quiser puxar a ART como
     certidao da empresa, chama
     `POST /api/v1/licitacoes/certidoes/importar-art` com {uf, numero}.
     O service consulta CREA, cria `CertidaoEmpresa`
     (tipo=ACERVO_TECNICO, numero/emissao/validade preenchidos a
     partir da resposta) e amarra a `crea_consultas.certidao_id`.

Convencoes seguidas:
  - service NUNCA chama `aclose()` no `client` -- caller mantem o
    lifecycle (singleton no router, factory no worker)
  - todos os erros viram row 'erro' em `crea_consultas` -- assim o
    historico ja serve de troubleshooting log sem precisar olhar
    journald do worker
  - mutacoes geram `audit_log` com actor real (passado pelo router via
    `current_user.email`)
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date as _date
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.integrations.infosimples.client import (
    CREA_TIPOS_SUPORTADOS,
    UFS_SUPORTADAS,
    InfosimplesCreaTipoNaoSuportadoError,
    InfosimplesUFNaoSuportadaError,
)
from app.modules.licitacoes.certidoes import (
    _AUDIT_RESOURCE as _CERTIDAO_AUDIT_RESOURCE,
)
from app.modules.licitacoes.certidoes import create_certidao
from app.modules.licitacoes.models import (
    CREA_CONSULTA_ERRO,
    CREA_CONSULTA_MOCK,
    CREA_CONSULTA_OK,
    CREA_TIPO_ART,
    CertidaoEmpresa,
    ConsultaCrea,
)

logger = logging.getLogger(__name__)

# `ACERVO_TECNICO` ja consta em `TIPOS_VALIDOS` do D.6 (CertidaoTipo).
# Usamos esse tipo ao importar ART -- semanticamente, a ART vira um
# atestado de acervo da empresa pra fase de habilitacao.
_TIPO_CERTIDAO_AO_IMPORTAR_ART = "ACERVO_TECNICO"

_AUDIT_RESOURCE = "licitacoes.crea_consulta"
_AUDIT_ACTOR_PLACEHOLDER = _AUDIT_ACTOR_SYSTEM


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource: str,
    resource_id: int | None,
    metadata: dict[str, Any] | None = None,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id is not None else None,
            metadata_json=json.dumps(metadata, default=str) if metadata else None,
        )
    )
    await db.commit()


# -------------------------- consulta CREA -------------------------------


async def consultar_crea(
    db: AsyncSession,
    *,
    uf: str,
    tipo: str,
    identificador: str,
    client: Any,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> ConsultaCrea:
    """Dispara consulta CREA, persiste resultado e retorna a row.

    Sempre retorna uma `ConsultaCrea`: em sucesso (status='ok' ou 'mock')
    com `payload` populado, ou em erro (status='erro' + `error_msg`).
    Nao re-raise -- o endpoint devolve 200 com `status=erro` para a UI
    poder renderizar a tentativa no historico.

    Levanta apenas validacoes de input antes de gravar:
      - InfosimplesUFNaoSuportadaError (UF fora SP/MG/GO)
      - InfosimplesCreaTipoNaoSuportadoError (tipo invalido)
      - ValueError (identificador vazio / muito longo)

    Caller mantem o lifecycle do `client` (singleton em `manutencao_frota`).
    """
    uf_norm = uf.upper().strip()
    if uf_norm not in UFS_SUPORTADAS:
        raise InfosimplesUFNaoSuportadaError(
            f"UF {uf_norm!r} nao suportada. Disponiveis: "
            + ", ".join(sorted(UFS_SUPORTADAS))
        )
    tipo_norm = tipo.lower().strip()
    if tipo_norm not in CREA_TIPOS_SUPORTADOS:
        raise InfosimplesCreaTipoNaoSuportadoError(
            f"tipo {tipo!r} nao suportado. Use: "
            + ", ".join(sorted(CREA_TIPOS_SUPORTADOS))
        )
    ident_norm = (identificador or "").strip()
    if not ident_norm:
        raise ValueError("identificador vazio")
    if len(ident_norm) > 64:
        raise ValueError(
            f"identificador muito longo: {len(ident_norm)} chars (max 64)"
        )

    consulta = ConsultaCrea(
        uf=uf_norm,
        tipo=tipo_norm,
        identificador=ident_norm,
        status="pendente",
    )
    db.add(consulta)

    try:
        result = await client.consultar_crea(uf_norm, tipo_norm, ident_norm)
    except Exception as exc:  # noqa: BLE001 -- guardamos QUALQUER erro
        # Erros de transporte, code != 200, JSON invalido, etc. Tudo
        # vira row 'erro'; UI mostra inline. Nao tocamos `payload`
        # nem `source` -- ja vem com defaults do schema.
        logger.warning(
            "consulta_crea %s/%s/%s falhou: %s",
            uf_norm,
            tipo_norm,
            ident_norm,
            exc,
        )
        consulta.status = CREA_CONSULTA_ERRO
        consulta.error_msg = str(exc)[:500]
        consulta.source = (
            "infosimples_mock"
            if getattr(client, "is_mock", False)
            else "infosimples"
        )
        await db.commit()
        await _record_audit(
            db,
            action="error",
            resource=_AUDIT_RESOURCE,
            resource_id=consulta.id,
            actor=actor,
            metadata={
                "uf": uf_norm,
                "tipo": tipo_norm,
                "identificador": ident_norm,
                "error": str(exc)[:500],
            },
        )
        await db.refresh(consulta)
        return consulta

    consulta.payload = result
    consulta.source = result.get("source", "infosimples")
    consulta.status = (
        CREA_CONSULTA_MOCK
        if consulta.source.endswith("_mock")
        else CREA_CONSULTA_OK
    )
    await db.commit()
    await _record_audit(
        db,
        action="create",
        resource=_AUDIT_RESOURCE,
        resource_id=consulta.id,
        actor=actor,
        metadata={
            "uf": uf_norm,
            "tipo": tipo_norm,
            "identificador": ident_norm,
            "source": consulta.source,
            "situacao": _extract_situacao(result, tipo_norm),
        },
    )
    await db.refresh(consulta)
    return consulta


def _extract_situacao(payload: dict[str, Any], tipo: str) -> str | None:
    """Extrai o campo `situacao` aplicavel ao tipo (pra audit metadata)."""
    section = payload.get(tipo) or {}
    if isinstance(section, dict):
        return section.get("situacao")
    return None


# -------------------------- listagem ------------------------------------


async def list_consultas_crea(
    db: AsyncSession,
    *,
    tipo: str | None = None,
    identificador: str | None = None,
    uf: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[ConsultaCrea], int]:
    """Lista consultas CREA ordenadas por executed_at desc.

    Filtros opcionais:
      - `tipo`: 'art' | 'profissional' | 'empresa'
      - `identificador`: numero ART, registro CREA, ou CNPJ
      - `uf`: SP | MG | GO

    Retorna (rows, total) -- total e o count *antes* da paginacao.
    """
    base_filters: list[Any] = []
    if tipo:
        base_filters.append(ConsultaCrea.tipo == tipo.lower().strip())
    if identificador:
        base_filters.append(ConsultaCrea.identificador == identificador.strip())
    if uf:
        base_filters.append(ConsultaCrea.uf == uf.upper().strip())

    count_stmt = select(func.count(ConsultaCrea.id))
    list_stmt = (
        select(ConsultaCrea)
        .order_by(ConsultaCrea.executed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    for cond in base_filters:
        count_stmt = count_stmt.where(cond)
        list_stmt = list_stmt.where(cond)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = list((await db.execute(list_stmt)).scalars().all())
    return rows, int(total)


# -------------------------- import ART como certidao --------------------


async def importar_art_como_certidao(
    db: AsyncSession,
    *,
    uf: str,
    numero_art: str,
    empresa_cnpj: str,
    client: Any,
    actor: str = _AUDIT_ACTOR_PLACEHOLDER,
) -> tuple[ConsultaCrea, CertidaoEmpresa | None]:
    """Consulta ART no CREA e cria `CertidaoEmpresa` correspondente.

    Retorna `(consulta, certidao | None)`:
      - se a consulta retornar `status='erro'`, `certidao=None` e a
        consulta tem `error_msg` populado
      - se a consulta retornar 'ok'/'mock' e a ART nao tiver `numero`
        ou estiver em situacao terminal (`BAIXADA`/`CANCELADA`), tambem
        retornamos `certidao=None` e `error_msg='ART invalida ou
        nao-ativa'` -- a UI deve mostrar a consulta mas nao criar
        certidao automaticamente
      - caso contrario, `CertidaoEmpresa` e criada com:
          tipo='ACERVO_TECNICO'
          numero=art.numero
          emissao=art.data_registro (parseado pra date)
          validade=art.data_termino_previsto (idem)
          orgao_emissor='CREA-{UF}'
          observacoes='Importado de ART {numero} via Infosimples ({source})'

    A consulta tem `certidao_id` setado pra rastreabilidade.
    """
    consulta = await consultar_crea(
        db,
        uf=uf,
        tipo=CREA_TIPO_ART,
        identificador=numero_art,
        client=client,
        actor=actor,
    )
    if consulta.status == CREA_CONSULTA_ERRO:
        return consulta, None

    payload = consulta.payload or {}
    art = payload.get("art") or {}
    if not art.get("numero"):
        consulta.status = CREA_CONSULTA_ERRO
        consulta.error_msg = (
            "resposta CREA sem numero de ART -- nao pode importar como certidao"
        )
        await db.commit()
        return consulta, None

    situacao = (art.get("situacao") or "").upper()
    if situacao in {"BAIXADA", "CANCELADA"}:
        # Politica: nao importa ART terminada. Operador pode forcar
        # criando manualmente se quiser registrar historico.
        # Marca status='erro' pra UI renderizar a explicacao em vermelho
        # (mesma convencao da branch "sem numero" acima).
        consulta.status = CREA_CONSULTA_ERRO
        consulta.error_msg = (
            f"ART em situacao {situacao!r} -- nao importada (cadastre manual"
            " se quiser registrar como historico)"
        )
        await db.commit()
        return consulta, None

    emissao = _parse_iso_date(art.get("data_registro"))
    validade = _parse_iso_date(art.get("data_termino_previsto"))

    # `observacoes` documenta a origem -- util pra auditoria depois e
    # pra UI poder destacar "importada do CREA" vs cadastro manual.
    observacoes = (
        f"Importado de ART {art.get('numero')} via Infosimples ({consulta.source})."
    )
    if art.get("tipo_servico"):
        observacoes += f" Servico: {art['tipo_servico']}."
    if art.get("valor_contrato"):
        observacoes += f" Valor: R$ {art['valor_contrato']}."
    profissional = payload.get("profissional") or {}
    if profissional.get("nome"):
        observacoes += f" Profissional: {profissional['nome']}."

    certidao = await create_certidao(
        db,
        empresa_cnpj=empresa_cnpj,
        tipo=_TIPO_CERTIDAO_AO_IMPORTAR_ART,
        numero=art.get("numero"),
        emissao=emissao,
        validade=validade,
        orgao_emissor=f"CREA-{consulta.uf}",
        observacoes=observacoes[:2048],
        actor=actor,
    )
    consulta.certidao_id = certidao.id
    await db.commit()
    await _record_audit(
        db,
        action="import_art",
        resource=_CERTIDAO_AUDIT_RESOURCE,
        resource_id=certidao.id,
        actor=actor,
        metadata={
            "consulta_id": consulta.id,
            "uf": consulta.uf,
            "numero_art": art.get("numero"),
            "empresa_cnpj": empresa_cnpj,
            "situacao": art.get("situacao"),
        },
    )
    await db.refresh(consulta)
    await db.refresh(certidao)
    return consulta, certidao


def _parse_iso_date(value: Any) -> _date | None:
    """Parse 'YYYY-MM-DD' / 'DD/MM/YYYY' / ISO datetime, tolerante."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # ISO 8601 datetime ('2023-05-12T00:00:00Z' etc) -- delega pra fromisoformat.
    if "T" in s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    # Formatos puros de data.
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    logger.info("data CREA nao parseavel: %r", value)
    return None
