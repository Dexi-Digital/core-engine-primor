# Test report — PR #2 (D.2 busca full-text no objeto_compra)

**Summary (1 line):** testei o novo input "Busca no objeto" em `/licitacoes` contra os 197 PNCP reais ingeridos no PR #1 — 3 asserções passaram, 0 falharam.

**Escalações:** nenhuma. Uma observação de ambiente (não-bloqueante): a migração `3fbc4a1e8d57` não tinha sido aplicada no postgres local do sandbox (estava na rev do PR #1); rodei `alembic upgrade head` e o `similarity()` trigram passou a funcionar. Em CI/produção isso é feito pelo próprio deploy, não é bug de código.

---

## Resultados

| # | Test | Resultado |
|---|---|---|
| 1 | It should filter licitações by free-text search in objeto_compra | passou |
| 2 | It should show empty-state when the search term has no matches | passou |
| 3 | It should combine UF filter with search using AND semantics | passou |

## Evidências

### 🟢 Baseline — 197 rows

Input "Busca no objeto" presente com placeholder `ex: pavimentação asfáltica`. Contador: "197 licitações · página 1".

![baseline](https://app.devin.ai/attachments/7b5a91e2-8059-43fe-8d1b-31327e3b2966/screenshot_30b0289776de42518aee4ace66b656c3.png)

### 🟢 Test 1 — search=aquisicao filtra para 15 rows

URL: `/licitacoes?uf=&modalidade=&search=aquisicao`. Contador: **"15 licitações · página 1"**. Todas as 13 linhas visíveis têm "AQUISICAO"/"Aquisicao" no objeto. Sem paginação (15 < 20). Fonte dos dados: PNCP real, SP 2026-04-21..22.

![search aquisicao](https://app.devin.ai/attachments/96e7be9f-a620-4d2a-815c-091342c3b705/screenshot_ac70c5daf436427abe689bf00556c844.png)

### 🟢 Test 2 — search=zzzzz → empty-state

URL: `/licitacoes?uf=&modalidade=&search=zzzzz`. Empty-state com texto literal **"Nenhuma licitação ingerida ainda. Dispare um crawler:"** + snippet curl. Confirma que o branch `resp.data.length === 0` do Server Component não quebra com o novo param.

![empty state](https://app.devin.ai/attachments/6e7c30f3-15ea-42bf-b077-c97a2f2fd353/screenshot_246f4bdcb8d149019a6594e59db25623.png)

### 🟢 Test 3 — uf=SP + search=aquisicao compõem em AND

URL: `/licitacoes?uf=SP&modalidade=&search=aquisicao`. Contador: **"15 licitações · página 1"** (mesmo resultado do Test 1 porque as 197 rows são todas SP — o que **prova** que UF + search são AND, não OR; se fosse OR sobraria 197).

![uf + search AND](https://app.devin.ai/attachments/00f76525-c7a8-4c0b-8b2f-57fb17a136f1/screenshot_d536e0ce507c40f1bf4eb57780c58c06.png)

## Recording

Vídeo da sessão de teste com anotações: https://app.devin.ai/attachments/d614979f-d6f0-4730-bf12-e8260d5ce37e/rec-57eebc77-3d21-4f3c-9b25-7589baaf62fa-subtitled.mp4

## Cobertura shell (adicional)

Verificado via curl direto na API antes da UI (mesma DB):

| `search=` | `total` retornado |
|---|---|
| *(vazio)* | 197 |
| `aquisicao` | 15 |
| `servico` | 8 |
| `medicamento` | 5 |
| `pavimenta` | 1 |
| `zzzzz` | 0 |

## Não testado nesta sessão

- Comparação direta do **ranking por similaridade** trigram no Postgres vs SQLite. O `similarity()` está sendo chamado no Postgres (confirmado pelo stacktrace que recebi antes de rodar a migração), mas não inspecionei a ordem de resultados contra uma query de controle. Se quiser fortalecer a prova, posso abrir uma issue de follow-up para adicionar um teste que insira 3 rows com graus de similaridade conhecidos.
- Performance sob carga (o índice GIN faz diferença só em datasets grandes; com 197 rows um seq scan já é rápido).
