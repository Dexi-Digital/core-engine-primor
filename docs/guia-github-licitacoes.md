# Guia de GitHub + Módulo de Licitações (para não técnicos)

> Para quem nunca usou GitHub e vai trabalhar **somente** na parte de
> **Licitações** da plataforma. Não é preciso saber programar para entender
> este guia: o objetivo é você conseguir acompanhar o trabalho, propor mudanças
> com segurança e nunca quebrar o que já está funcionando.

Este documento tem duas partes:

1. **[Parte 1 — GitHub na prática](#parte-1--github-na-prática)**: o que é, por
   que usamos *branches* e como é o fluxo do dia a dia da equipe.
2. **[Parte 2 — O módulo de Licitações](#parte-2--o-módulo-de-licitações)**:
   o que ele faz, o que a equipe vê na tela e onde cada coisa mora no projeto.

---

## Parte 1 — GitHub na prática

### O que é o GitHub (em uma frase)

O GitHub é o lugar onde guardamos **todo o código da plataforma** com um
histórico completo de **quem mudou o quê e quando**. Pense nele como um Google
Drive para código, com duas diferenças importantes:

- Ele **guarda todas as versões**: nada se perde, dá para voltar atrás sempre.
- Mudanças só entram na versão "oficial" **depois de revisadas e aprovadas**.

### Os 4 conceitos que você precisa conhecer

| Termo | O que é | Analogia |
|-------|---------|----------|
| **Repositório** (*repo*) | A pasta do projeto inteiro, com todo o histórico. | O arquivo-mãe da empresa. |
| **Branch** (*ramo*) | Uma **cópia paralela** do projeto onde você trabalha sem afetar os outros. | Uma "via de rascunho" do documento. |
| **Commit** | Uma mudança salva, com uma mensagem explicando o que foi feito. | Um "salvar com descrição". |
| **Pull Request** (*PR*) | Um pedido formal para juntar o seu rascunho à versão oficial, que passa por revisão. | Enviar o documento para o chefe aprovar. |

### Por que usamos branches (a parte mais importante)

Existe uma branch principal chamada **`main`**. Ela é a versão **oficial e que
está no ar**. A regra de ouro é:

> ⚠️ **Ninguém mexe direto na `main`.** Todo trabalho acontece primeiro em uma
> branch separada.

Por quê? Porque assim:

- Várias pessoas trabalham ao mesmo tempo sem atrapalhar umas às outras.
- Se algo der errado no seu rascunho, a versão oficial continua intacta.
- Cada mudança é revisada **antes** de chegar no que está funcionando.

Visualmente:

```
main (oficial, no ar)  ●──────●──────●──────●────────────▶
                              \                    ▲
 sua branch de licitações      ●────●────●────────╯
                              (você trabalha aqui)   (depois de aprovado,
                                                      volta para a main)
```

### Como nomear sua branch

Para a equipe de licitações, use sempre este padrão, **tudo em minúsculas e sem
acento**:

```
licitacoes/<seu-nome>-<descricao-curta>
```

Exemplos:

- `licitacoes/maria-ajuste-filtro-uf`
- `licitacoes/joao-texto-boletim-email`
- `licitacoes/ana-novo-campo-certidao`

Assim qualquer pessoa olha o nome e já sabe **quem** está mexendo e **em quê**,
dentro do módulo de licitações.

### O fluxo do dia a dia (passo a passo)

Você pode fazer tudo isso pelo **site do GitHub** (não precisa instalar nada) ou
pedir ajuda de um técnico para a parte de código. O ciclo é sempre o mesmo:

1. **Crie uma branch** a partir da `main`, com o nome no padrão acima.
2. **Faça as mudanças** (texto, ajustes, conteúdo) e salve em **commits** com
   mensagens claras — ex.: `ajusta texto do email de boletim`.
3. **Abra um Pull Request (PR)** descrevendo o que mudou e por quê.
4. **Aguarde a revisão**: um colega técnico revisa e o robô de testes (CI)
   confere se nada quebrou.
5. **Depois de aprovado**, o PR é juntado (*merge*) à `main` e a mudança entra no ar.

### Boas práticas (e o que evitar)

✅ **Faça:**
- Uma branch para **cada tarefa** (não acumule várias coisas diferentes na mesma).
- Mensagens de commit curtas e claras, descrevendo o **resultado**.
- No PR, diga **qual demanda de licitações** está sendo atendida.

❌ **Evite:**
- Mexer na branch `main` diretamente.
- Misturar mudanças de licitações com outras áreas (RH, frota, etc.).
- Apagar branches ou arquivos sem combinar com a equipe.

### Glossário rápido

- **CI**: robô que roda os testes automaticamente em cada PR. Se ficar
  vermelho ❌, algo quebrou e precisa de ajuste antes de aprovar.
- **Merge**: ato de juntar sua branch aprovada na `main`.
- **Revisão / Review**: quando outra pessoa lê suas mudanças e comenta antes de aprovar.
- **Conflito**: quando duas pessoas mexeram na mesma linha; um técnico ajuda a resolver.

---

## Parte 2 — O módulo de Licitações

> A partir daqui, falamos **única e exclusivamente** do módulo de Licitações
> (chamado internamente de **Módulo D**). Ele atende a demanda de
> **inteligência de licitações**: encontrar oportunidades de contratos
> públicos, baixar editais, analisá-los e manter a documentação da empresa em dia.

### O que o módulo faz

Em linguagem simples, o módulo de licitações:

1. **Busca licitações públicas** automaticamente em portais do governo.
2. **Baixa o edital e os anexos** de cada licitação de interesse.
3. **Analisa o edital com Inteligência Artificial** para resumir prazos,
   exigências e pontos de atenção.
4. **Envia boletins por email** com as novas oportunidades que combinam com os
   filtros que você salvou.
5. **Controla certidões e atestados** da empresa, avisando quando estão
   **vencendo** — para não perder uma licitação por documento vencido.

### De onde vêm os dados (as fontes)

- **PNCP** (Portal Nacional de Contratações Públicas) — **fonte principal** das
  licitações e dos editais.
- **Conlicitação**, **ComprasNet** e **Licitações-e** — fontes complementares
  (parte já no roadmap/futuro).
- **CREA / ART** — consulta de registros técnicos da empresa.
- **Email (Resend)** — envio dos boletins e alertas.
- **OneDrive** — onde os anexos dos editais podem ser guardados.

### O que a equipe vê na tela (as 4 telas)

Dentro do painel, em **Licitações**, existem quatro páginas:

| Tela | Para que serve |
|------|----------------|
| **Licitações** (lista) | Lista as licitações encontradas. Dá para filtrar por **UF**, **modalidade** (ex.: Pregão) e **buscar pelo objeto** (ex.: "pavimentação asfáltica"). |
| **Detalhe da licitação** | Mostra os dados de uma licitação, permite **baixar o edital + anexos** e ver a **análise feita pela IA**. |
| **Boletins por email** | Cria "consultas salvas" (filtros) e define **quem recebe** os boletins. Os envios saem **3x ao dia (07h, 13h, 19h)**. |
| **Certidões e atestados** | Cadastra certidões da empresa e mostra o status: **vigente**, **vencendo**, **vencido** ou **sem validade**. |

### As entregas internas (sub-demandas "D.x")

Quando você vir essas siglas em PRs ou conversas, é a isto que se referem —
tudo dentro de licitações:

- **D.3 — Boletins por email**: filtros salvos + envio automático 3x/dia.
- **D.4 — Download de edital**: puxar edital e anexos do PNCP.
- **D.5 — Análise do edital com IA**: extrair prazos, exigências e riscos.
- **D.6 — Certidões / CREA**: controle de validade e alertas de vencimento (1x/dia).

### Onde cada coisa mora (mapa do código)

Você normalmente **não vai editar** esses arquivos sozinho, mas conhecer o mapa
ajuda a conversar com os técnicos e a entender os PRs. **Tudo de licitações está
concentrado nestes lugares:**

```
apps/
  api/app/modules/licitacoes/      ← o coração do módulo (regras de negócio)
    service.py        lista e busca de licitações
    editais.py        download de edital + anexos        (D.4)
    analise.py        análise do edital com IA            (D.5)
    boletins.py       filtros salvos + boletins por email (D.3)
    certidoes.py      certidões/atestados + alertas       (D.6)
    crea_service.py   consulta CREA/ART
    router.py         as "portas de entrada" da API de licitações
    models.py         como os dados são guardados no banco
    schemas.py        formato dos dados que entram e saem

  api/app/integrations/            ← conversa com os portais externos
    pncp/             Portal Nacional de Contratações Públicas (principal)
    conlicitacao/  comprasnet/  licitacoes_e/   (fontes complementares)

  workers/worker/tasks/licitacoes.py   ← tarefas automáticas (rodam sozinhas):
    crawler_pncp              busca licitações periodicamente
    download_edital           baixa editais em segundo plano
    dispatch_boletins         envia os boletins 3x/dia
    dispatch_certidao_alerts  envia alertas de certidão 1x/dia

  web/src/app/(dashboard)/licitacoes/   ← as telas que a equipe usa
    page.tsx            lista de licitações
    [id]/page.tsx       detalhe + edital + análise IA
    boletins/page.tsx   boletins por email
    certidoes/page.tsx  certidões e atestados
```

### Regra de ouro do módulo

> Trabalhe **apenas** dentro das pastas de licitações listadas acima. Se uma
> mudança parecer exigir mexer em outro módulo (RH, frota, financeiro...),
> **pare e fale com um técnico** — provavelmente há um caminho melhor.

---

## Resumo de uma página

1. Nunca mexa na `main` direto. Crie uma branch `licitacoes/<seu-nome>-<descricao>`.
2. Salve em **commits** com mensagens claras.
3. Abra um **Pull Request** dizendo o que mudou e qual demanda de licitações atende.
4. Espere a **revisão** e o **CI** ficarem verdes; só então faz **merge** na `main`.
5. Tudo de licitações vive em `apps/api/app/modules/licitacoes/`,
   `apps/workers/worker/tasks/licitacoes.py` e
   `apps/web/src/app/(dashboard)/licitacoes/`. Fora disso, chame um técnico.
