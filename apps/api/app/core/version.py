"""Impressao digital do codigo da aplicacao, para detectar defasagem.

**O problema que isto resolve.** A imagem do worker carrega o pacote da
API dentro dela (necessario: as tasks importam `app.*`). Entao toda
alteracao em `apps/api` exige redeploy dos DOIS servicos. Nada avisa
quando eles divergem -- e a divergencia e silenciosa: cada servico
funciona, so que com regras diferentes.

Aconteceu em 16/09/2026: o PR #67 mudou o gatilho de manutencao
preventiva de 250h para 50h; a API subiu com a regra nova e o worker
ficou com a antiga. Os dois discordariam sobre quando alertar, sem erro
nenhum em lugar nenhum.

**Por que hash do codigo e nao o commit do git.** O `.git` nao entra no
contexto de build (esta no `.dockerignore`), e `railway up` sobe um
diretorio local -- nao ha commit confiavel dentro da imagem. O hash do
conteudo de `app/**/*.py` e derivado da propria imagem: se dois
servicos tem o mesmo codigo, tem o mesmo hash, independente de como
foram construidos.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_RAIZ_PACOTE = Path(__file__).resolve().parent.parent  # .../app


@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """Hash estavel do codigo do pacote `app`. 12 hex, curto de ler.

    Cacheado: o codigo nao muda em runtime, e recalcular a cada chamada
    custaria I/O a toa.
    """
    h = hashlib.sha256()
    arquivos = sorted(
        p for p in _RAIZ_PACOTE.rglob("*.py") if "__pycache__" not in p.parts
    )
    for caminho in arquivos:
        # O caminho relativo entra no hash: mover arquivo e mudanca.
        h.update(str(caminho.relative_to(_RAIZ_PACOTE)).encode())
        h.update(caminho.read_bytes())
    return h.hexdigest()[:12]
