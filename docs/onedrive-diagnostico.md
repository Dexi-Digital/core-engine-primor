# Diagnostico de Estrutura OneDrive

Modulo que valida se a pasta raiz do OneDrive/SharePoint segue o padrao de
pastas esperado e se cada entidade cadastrada no DB tem todos os documentos
obrigatorios.

Complementa o **D1 Diagnostico Documental** (que le das tabelas de DB) e o
**OneDrive Sync** (que le do SharePoint -> DB). Esse aqui compara o que
**existe** (SharePoint) vs o que **deveria existir** (DB + spec).

## Padrao de pastas esperado

```
<root>/                               (default: MotorCentral/editais)
  dp/<employee_id>/<DOC_TIPO>.ext
  frota/<PLACA>/<DOC_TIPO>.ext
  obras/<OBRA_CODIGO>/<DOC_TIPO>.ext
  empresa/<DOC_TIPO>.ext              (singleton -- sem subpasta de entidade)
```

Mesmo padrao do `onedrive_sync.parser` (PR #26). O diagnostico aceita pastas
de frota/obras com ou sem hifen na identificacao (placa `ABC-1D23` ou
`ABC1D23`).

## Docs obrigatorios por area

Definidos em [`apps/api/app/modules/onedrive_diagnostico/spec.py`](../apps/api/app/modules/onedrive_diagnostico/spec.py).
V1 e hardcoded — UI de configuracao fica pra PR seguinte. Extraido dos
tipos validos ja definidos pelos modulos DP, manutencao e licitacoes.

Resumo:

| Area    | Obrigatorios (V1)                                     |
|---------|-------------------------------------------------------|
| dp      | CTPS, CONTRATO_TRABALHO, FICHA_REGISTRO               |
| frota   | CRLV, SEGURO, IPVA                                    |
| obras   | ART, ALVARA                                           |
| empresa | CONTRATO_SOCIAL                                       |

Os demais tipos sao **reconhecidos** (nao geram `extra`) mas nao geram
`faltando` quando ausentes.

## Tipos de finding

| Kind                  | Significado                                                                     |
|-----------------------|---------------------------------------------------------------------------------|
| `ok`                  | Documento obrigatorio presente                                                  |
| `faltando`            | Doc obrigatorio nao esta no OneDrive pra essa entidade                          |
| `extra`               | Arquivo com tipo desconhecido (fora da spec) em pasta conhecida                 |
| `fora_do_padrao`      | Caminho nao casa com a convencao (area invalida, profundidade errada, etc.)    |
| `entidade_fantasma`   | Pasta existe no OneDrive mas nao ha entidade correspondente no DB              |
| `entidade_sem_pasta`  | Entidade existe no DB mas nao tem pasta no OneDrive (ou a pasta esta vazia)    |

## Uso

### API

```
GET /api/v1/diagnostico/onedrive?area=all|dp|frota|obras|empresa
```

Resposta:

```json
{
  "root_folder": "MotorCentral/editais",
  "total_files": 42,
  "counts": {"ok": 30, "faltando": 5, "extra": 2, "fora_do_padrao": 0,
             "entidade_fantasma": 1, "entidade_sem_pasta": 4},
  "by_area": {
    "dp": [
      {
        "area": "dp",
        "entity_key": "42",
        "label": "Joao Silva (CPF 111.111.111-11)",
        "ok": false,
        "findings": [
          {"kind": "faltando", "area": "dp", "doc_tipo": "CTPS",
           "entity_key": "42", "entity_label": "Joao Silva (CPF 111.111.111-11)",
           "path": null, "message": "doc obrigatorio ausente: CTPS"}
        ]
      }
    ]
  }
}
```

### UI

Pagina `/diagnostico/onedrive` na app Next (apps/web). Combobox pra filtrar
area, cards de contadores, lista colapsavel por entidade com problemas.

### Worker

Task agendada `worker.tasks.onedrive_diagnostico.run_diagnostico` roda
segunda-feira as 05h America/Sao_Paulo e loga o resumo. V1 nao persiste
snapshot nem envia email — ambos previstos pra follow-ups.

## Backend de storage

Mesma chaveamento do resto da app: `STORAGE_BACKEND=onedrive` + variaveis
`MS_GRAPH_*` -> client real contra o tenant PRIMOR. Caso contrario, usa
`OneDriveMockClient` em memoria (util pros testes).

## Arquitetura

- `spec.py` — spec por area (hardcoded V1).
- `service.py` — `_compute_findings` e funcao pura sobre (items, indices de
  entidades). `run_diagnostico` e o wrapper I/O (Graph walk + DB load).
- `router.py` — endpoint FastAPI protegido por JWT.
- `schemas.py` — serialization Pydantic.
- `tests/test_onedrive_diagnostico.py` — cobre cada finding kind, filtro de
  area, normalizacao de placa.

## Evolucao prevista (nao neste PR)

1. Snapshot persistente em tabela `onedrive_diagnostico_runs` + historico.
2. Alerta por email (Resend) quando `faltando` ou `entidade_fantasma` > 0.
3. Spec configuravel via UI (YAML ou form) — hoje e hardcoded.
4. Export PDF do relatorio.
