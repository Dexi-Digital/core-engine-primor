"""Diagnostico da estrutura de pastas do OneDrive.

Compara o que ESTA no OneDrive (sob `MS_GRAPH_ROOT_FOLDER`) com o
que DEVERIA estar, com base em:
  - entidades no DB (funcionarios, veiculos, obras, empresa)
  - docs obrigatorios por area (hardcoded em `spec.py`, evolui depois)
  - convencao de pastas do OneDrive sync (PR #26):
      dp/<employee_id>/<DOC_TIPO>.<ext>
      frota/<PLACA>/<DOC_TIPO>.<ext>
      obras/<OBRA_CODIGO>/<DOC_TIPO>.<ext>
      empresa/<DOC_TIPO>.<ext>

Retorna lista de diferencas por entidade:
  - faltando         -- doc obrigatorio nao existe no OneDrive
  - extra            -- arquivo com doc_tipo nao reconhecido naquela area
  - fora_do_padrao   -- path nao casa com a convencao
  - entidade_fantasma-- pasta de entidade que nao existe no DB
  - entidade_sem_pasta-- entidade no DB sem pasta correspondente
"""
