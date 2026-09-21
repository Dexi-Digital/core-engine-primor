"""Importa TODOS os modulos de models, registrando-os no `Base.metadata`.

Existe porque o SQLAlchemy so resolve foreign keys entre tabelas que
estejam no mesmo `metadata` no momento em que configura os mappers. Um
processo que importa so parte dos models quebra na primeira query que
toque uma tabela com FK para uma tabela ausente -- com um erro que nao
diz "faltou importar", e sim:

    NoReferencedTableError: Foreign key associated with column
    'dp_employee_documents.onedrive_sync_run_id' could not find table
    'onedrive_sync_runs'

Aconteceu em producao em 13/09/2026: `scripts/pull_onsafety.py`
importava `dp_sesmt` e `obras`, mas nao `onedrive_sync` -- e o
`EmployeeDocument` tem FK para `onedrive_sync_runs`. A API nunca sofreu
disso porque `app.main` monta todos os routers e acaba importando tudo.

QUALQUER entrypoint que nao seja a API (scripts, jobs, tasks avulsas)
deve importar este modulo antes de tocar o banco.
"""
from __future__ import annotations

from app.audit import models as _audit  # noqa: F401
from app.core.db import Base  # noqa: F401
from app.modules.auth import models as _auth  # noqa: F401
from app.modules.diagnostico import models as _diag  # noqa: F401
from app.modules.dp_sesmt import afastamentos as _afast  # noqa: F401
from app.modules.dp_sesmt import models as _dp  # noqa: F401
from app.modules.financeiro_contratos import models as _contratos  # noqa: F401
from app.modules.financeiro_totvs import models as _totvs  # noqa: F401
from app.modules.fiscal import models as _fiscal  # noqa: F401
from app.modules.juridico import models as _jur  # noqa: F401
from app.modules.licitacoes import models as _lic  # noqa: F401
from app.modules.manutencao_frota import models as _frota  # noqa: F401
from app.modules.notificacoes import models as _notif  # noqa: F401
from app.modules.obras import models as _obras  # noqa: F401
from app.modules.onedrive_sync import models as _onedrive  # noqa: F401
from app.modules.ponto import models as _ponto  # noqa: F401
