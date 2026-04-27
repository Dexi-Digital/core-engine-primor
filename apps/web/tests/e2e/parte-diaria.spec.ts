/**
 * E2E do PWA `/m/parte-diaria` (PR #29).
 *
 * Cobertura:
 *   1. Online happy-path -> POST 201 -> "Parte salva" + form reset
 *   2. Offline submit (`context.setOffline(true)`) -> "Sem sinal" + 1 row
 *      no IndexedDB com client_uuid v4
 *   3. Drena fila quando volta online -> evento `online` -> drainQueue
 *      reenvia 2 partes -> "Sincronizadas 2 parte(s)" + IndexedDB vazio
 *   4. 503 fallback (Next.js de pe, FastAPI fora) -> route mocka 503 ->
 *      "Sem conexao -- parte salva localmente" + 1 row na fila
 *   5. Idempotencia cross-tab -> drainQueue topa POST 200 (ja existia)
 *      sem erro, marca synced, sem dupes
 *
 * Estrategia de mock:
 *   - `page.route("**\/m/api/veiculos")` -> 200 com lista de veiculos
 *   - `page.route("**\/m/api/parte-diaria")` -> 201/200/503 conforme spec
 *   - `context.setOffline()` para alternar conectividade do navegador
 *   - Service Worker e cookie de auth nao sao exercitados (auth fica no
 *     route handler que e bypassado pelo mock; SW e best-effort no form
 *     e nao afeta IndexedDB queue)
 */
import { test, expect, type Page } from "@playwright/test";

type MockedPostBehavior =
  | { kind: "success"; status?: 201 | 200 }
  | { kind: "unavailable" }
  | { kind: "error" };

type ParteRequestBody = {
  client_uuid?: string;
  veiculo_id?: number | null;
  data?: string | null;
  operador?: string | null;
  obra?: string | null;
  horimetro_inicio?: string | null;
  horimetro_fim?: string | null;
  combustivel_litros?: string | null;
  combustivel_custo?: string | null;
};

const VEICULO_FIXTURES = [
  { id: 1, placa: "ABC1D23", descricao: "Escavadeira CAT 320" },
  { id: 2, placa: "XYZ9K88", descricao: "Caminhao Volvo FMX" },
];

/**
 * Configura mocks default. `parteResponses` permite override por chamada
 * (cada item da fila no drain test, p.ex.). `recordedRequests` coleta
 * os bodies recebidos no POST pra asserts de idempotencia.
 */
async function installRouteMocks(
  page: Page,
  options: {
    parteResponses?: MockedPostBehavior[];
    recordedRequests?: ParteRequestBody[];
  } = {},
) {
  const { parteResponses, recordedRequests } = options;
  let postIdx = 0;

  await page.route("**/m/api/veiculos", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: VEICULO_FIXTURES }),
    });
  });

  await page.route("**/m/api/parte-diaria", async (route, request) => {
    if (request.method() !== "POST") {
      await route.continue();
      return;
    }
    let body: ParteRequestBody = {};
    try {
      body = JSON.parse(request.postData() ?? "{}") as ParteRequestBody;
    } catch {
      // body invalido -- deixa registrado vazio
    }
    if (recordedRequests) {
      recordedRequests.push(body);
    }
    const behavior =
      parteResponses?.[postIdx] ?? { kind: "success", status: 201 };
    postIdx += 1;

    if (behavior.kind === "unavailable") {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "API indisponivel" }),
      });
      return;
    }
    if (behavior.kind === "error") {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "boom" }),
      });
      return;
    }
    const now = new Date().toISOString();
    await route.fulfill({
      status: behavior.status ?? 201,
      contentType: "application/json",
      body: JSON.stringify({
        id: 100 + postIdx,
        client_uuid: body.client_uuid,
        veiculo_id: body.veiculo_id ?? null,
        data: body.data ?? null,
        ocr_status: "revisado",
        created_at: now,
      }),
    });
  });
}

/**
 * Limpa IndexedDB do PWA antes de cada spec -- garante isolamento.
 * Tem que rodar DEPOIS de `goto` na primeira vez, pra garantir que o
 * domain ta carregado.
 */
async function clearPwaQueue(page: Page) {
  await page.evaluate(async () => {
    await new Promise<void>((resolve) => {
      const req = indexedDB.deleteDatabase("mc-pwa-pd");
      req.onsuccess = () => resolve();
      req.onerror = () => resolve();
      req.onblocked = () => resolve();
    });
  });
}

/**
 * Conta itens na fila offline (IndexedDB store `pending`).
 */
async function queueLength(page: Page): Promise<number> {
  return await page.evaluate(async () => {
    return await new Promise<number>((resolve, reject) => {
      const open = indexedDB.open("mc-pwa-pd", 1);
      open.onsuccess = () => {
        const db = open.result;
        if (!db.objectStoreNames.contains("pending")) {
          db.close();
          resolve(0);
          return;
        }
        const tx = db.transaction("pending", "readonly");
        const req = tx.objectStore("pending").count();
        req.onsuccess = () => {
          resolve(req.result);
          db.close();
        };
        req.onerror = () => {
          db.close();
          reject(req.error);
        };
      };
      open.onerror = () => reject(open.error);
      // Se o DB ainda nao foi criado (sem submit offline), `onupgradeneeded`
      // dispara aqui -- consideramos 0 e fechamos.
      open.onupgradeneeded = () => {
        open.result.close();
        resolve(0);
      };
    });
  });
}

/**
 * Le todos os items da fila pra checar campos especificos (ex: client_uuid).
 */
async function readQueue(page: Page): Promise<
  Array<{ client_uuid: string; payload: Record<string, unknown> }>
> {
  return await page.evaluate(async () => {
    return await new Promise<
      Array<{ client_uuid: string; payload: Record<string, unknown> }>
    >((resolve, reject) => {
      const open = indexedDB.open("mc-pwa-pd", 1);
      open.onsuccess = () => {
        const db = open.result;
        if (!db.objectStoreNames.contains("pending")) {
          db.close();
          resolve([]);
          return;
        }
        const tx = db.transaction("pending", "readonly");
        const req = tx.objectStore("pending").getAll();
        req.onsuccess = () => {
          resolve(
            req.result as Array<{
              client_uuid: string;
              payload: Record<string, unknown>;
            }>,
          );
          db.close();
        };
        req.onerror = () => {
          db.close();
          reject(req.error);
        };
      };
      open.onerror = () => reject(open.error);
      open.onupgradeneeded = () => {
        open.result.close();
        resolve([]);
      };
    });
  });
}

/**
 * Preenche o form com valores validos. Default reusa veiculo 1 (ABC1D23).
 */
async function fillForm(
  page: Page,
  overrides: {
    veiculoId?: string;
    operador?: string;
    obra?: string;
    horimetroInicio?: string;
    horimetroFim?: string;
    combustivelLitros?: string;
    combustivelCusto?: string;
  } = {},
) {
  await page
    .getByLabel("Veiculo")
    .selectOption(overrides.veiculoId ?? "1");
  await page
    .getByLabel("Operador")
    .fill(overrides.operador ?? "Joao da Silva");
  await page.getByLabel("Obra / Frente").fill(overrides.obra ?? "OBR-001");
  await page
    .getByLabel("Horimetro inicio")
    .fill(overrides.horimetroInicio ?? "1000");
  await page
    .getByLabel("Horimetro fim")
    .fill(overrides.horimetroFim ?? "1008");
  await page
    .getByLabel("Combustivel (L)")
    .fill(overrides.combustivelLitros ?? "60");
  await page.getByLabel("Custo (R$)").fill(overrides.combustivelCusto ?? "420");
}

/**
 * Texto do botao de submit varia conforme estado:
 *   - Online: "Enviar"
 *   - Offline: "Salvar offline"
 *   - Submitting: "Salvando..."
 *
 * Regex unica que casa com os 3 estados (e nao casa com o botao
 * "Sincronizar agora" do banner de fila).
 */
const SUBMIT_BUTTON_RE = /^(Enviar|Salvar offline|Salvando)/;

const UUID_V4_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * Setup compartilhado: instala mocks de rota, autentica via cookie e
 * navega pra pagina (sem cache de IndexedDB de testes anteriores).
 * Cada test escolhe seu `parteResponses` / `recordedRequests` aqui.
 *
 * Importante instalar mocks ANTES do `goto` -- o useEffect do form
 * dispara fetch de /m/api/veiculos no mount; se a rota nao tiver
 * mock ja registrado, o fetch vai pro backend real configurado em
 * NEXT_PUBLIC_API_BASE_URL (porta 9 em testes) e falha, deixando
 * o dropdown sem opcoes.
 */
async function setupTest(
  page: Page,
  context: import("@playwright/test").BrowserContext,
  baseURL: string | undefined,
  options: {
    parteResponses?: MockedPostBehavior[];
    recordedRequests?: ParteRequestBody[];
  } = {},
) {
  // Middleware do Next exige cookie `mc_access_token`. O conteudo nao
  // e validado pelo middleware (so checa existencia) -- a validacao
  // JWT real fica no FastAPI, que aqui esta mockado via route().
  const url = new URL(baseURL ?? "http://localhost:3100");
  await context.addCookies([
    {
      name: "mc_access_token",
      value: "test-fake-jwt",
      domain: url.hostname,
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  // Com `serviceWorkers: 'block'` o registro do SW e bloqueado mas
  // `navigator.serviceWorker.ready` (usado em form.tsx pra agendar
  // Background Sync no submit offline) fica pending pra sempre ->
  // botao trava em "Salvando..." apos enqueue. Stubamos `ready` pra
  // rejeitar imediatamente; form ja tem try/catch tolerando isso.
  await page.addInitScript(() => {
    const sw = navigator.serviceWorker;
    if (sw) {
      Object.defineProperty(sw, "ready", {
        configurable: true,
        get() {
          return Promise.reject(
            new Error("service worker disabled in test"),
          );
        },
      });
    }
  });
  await installRouteMocks(page, options);
  // Vai pra pagina ja com mocks ativos.
  await page.goto("/m/parte-diaria");
  // Espera o veiculos fetch responder e popular o dropdown -- evita
  // race onde fillForm tenta selectOption antes das options renderem.
  await expect(
    page.locator('select >> option[value="1"]').first(),
  ).toBeAttached({ timeout: 10_000 });
  // IndexedDB residual de specs anteriores (mesmo origin).
  await clearPwaQueue(page);
}

test.describe("/m/parte-diaria PWA", () => {
  // Sem reuso de SW entre specs -- garante isolamento.
  test.beforeEach(async ({ context }) => {
    await context.clearCookies();
  });

  test("submete online com sucesso (201) e reseta o form", async ({
    page,
    context,
    baseURL,
  }) => {
    const recorded: ParteRequestBody[] = [];
    await setupTest(page, context, baseURL, { recordedRequests: recorded });

    await expect(page.getByTestId("conn-indicator")).toHaveText(/Online/);
    await fillForm(page);
    await page.getByRole("button", { name: SUBMIT_BUTTON_RE }).click();

    await expect(
      page.getByText(/parte salva/i, { exact: false }),
    ).toBeVisible();
    // Form deve resetar veiculo_id (volta pra "-- selecione --")
    await expect(page.getByLabel("Veiculo")).toHaveValue("");
    // ...mas preserva data + obra (para apontar varias partes seguidas
    // do mesmo turno sem redigitar).
    await expect(page.getByLabel("Obra / Frente")).toHaveValue("OBR-001");

    // Asserts: 1 request POST, com client_uuid v4 e payload coerente.
    expect(recorded).toHaveLength(1);
    const sent = recorded[0]!;
    expect(sent.client_uuid).toMatch(UUID_V4_RE);
    expect(sent.veiculo_id).toBe(1);
    expect(sent.operador).toBe("Joao da Silva");
    expect(sent.combustivel_litros).toBe("60");

    // IndexedDB queue continua vazia (online -> direto pra API).
    expect(await queueLength(page)).toBe(0);
  });

  test("offline -> grava no IndexedDB com client_uuid v4 e mostra fila", async ({
    page,
    context,
    baseURL,
  }) => {
    await setupTest(page, context, baseURL);

    await context.setOffline(true);
    // Browser nem sempre dispara `offline` synchronously apos
    // `setOffline` -- forca o evento pra UI atualizar o badge.
    await page.evaluate(() => window.dispatchEvent(new Event("offline")));
    await expect(page.getByTestId("conn-indicator")).toHaveText(/Offline/);

    await fillForm(page, { obra: "OBR-002" });
    await page.getByRole("button", { name: SUBMIT_BUTTON_RE }).click();

    await expect(
      page.getByText(/sem sinal -- parte salva localmente/i),
    ).toBeVisible();
    // Banner de fila aparece com contador.
    await expect(
      page.getByText(/1 parte\(s\) aguardando sincronizar/i),
    ).toBeVisible();

    const items = await readQueue(page);
    expect(items).toHaveLength(1);
    expect(items[0]!.client_uuid).toMatch(UUID_V4_RE);
    expect(items[0]!.payload.veiculo_id).toBe(1);
    expect(items[0]!.payload.obra).toBe("OBR-002");
  });

  test("voltar online drena a fila e limpa o IndexedDB", async ({
    page,
    context,
    baseURL,
  }) => {
    const recorded: ParteRequestBody[] = [];
    await setupTest(page, context, baseURL, { recordedRequests: recorded });

    // Submete 2 partes offline
    await context.setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event("offline")));

    await fillForm(page, { obra: "TURNO-A" });
    await page.getByRole("button", { name: SUBMIT_BUTTON_RE }).click();
    await expect(
      page.getByText(/1 parte\(s\) aguardando sincronizar/i),
    ).toBeVisible();

    await fillForm(page, { obra: "TURNO-B", horimetroInicio: "1010", horimetroFim: "1018" });
    await page.getByRole("button", { name: SUBMIT_BUTTON_RE }).click();
    await expect(
      page.getByText(/2 parte\(s\) aguardando sincronizar/i),
    ).toBeVisible();

    expect(await queueLength(page)).toBe(2);

    // Volta online -- Chromium dispara `online` no window automaticamente
    // apos `setOffline(false)`, o que aciona o listener do form e dispara
    // sync()/drainQueue. NAO dispatchamos manualmente alem disso porque
    // dois `online` events seguidos chamam sync() em paralelo: ambas as
    // chamadas fazem listPending antes de markSynced limpar, e cada item
    // acaba sendo POSTado 2x (ainda que com mesmo client_uuid -- backend
    // dedup, mas o assert de `recorded.length` quebra).
    await context.setOffline(false);

    // Banner de pendentes some assim que drainQueue resolve
    await expect(
      page.getByText(/parte\(s\) aguardando sincronizar/i),
    ).toHaveCount(0, { timeout: 10_000 });
    await expect(
      page.getByText(/sincronizadas 2 parte\(s\)/i),
    ).toBeVisible();

    expect(await queueLength(page)).toBe(0);
    // Backend recebeu os 2 com client_uuids distintos
    expect(recorded).toHaveLength(2);
    expect(recorded[0]!.client_uuid).toMatch(UUID_V4_RE);
    expect(recorded[1]!.client_uuid).toMatch(UUID_V4_RE);
    expect(recorded[0]!.client_uuid).not.toBe(recorded[1]!.client_uuid);
  });

  test("503 do route handler (FastAPI fora) cai no fallback offline", async ({
    page,
    context,
    baseURL,
  }) => {
    await setupTest(page, context, baseURL, {
      parteResponses: [{ kind: "unavailable" }],
    });

    // Online no navegador, mas API responde 503
    await expect(page.getByTestId("conn-indicator")).toHaveText(/Online/);

    await fillForm(page, { obra: "OBR-503" });
    await page.getByRole("button", { name: SUBMIT_BUTTON_RE }).click();

    await expect(
      page.getByText(/sem conexao -- parte salva localmente/i),
    ).toBeVisible();
    await expect(
      page.getByText(/1 parte\(s\) aguardando sincronizar/i),
    ).toBeVisible();
    expect(await queueLength(page)).toBe(1);
  });

  test("idempotencia: drainQueue aceita 200 (ja existia) sem duplicar", async ({
    page,
    context,
    baseURL,
  }) => {
    const recorded: ParteRequestBody[] = [];
    // Primeiro POST do drain volta 200 (idempotente). drainQueue deve
    // aceitar como sucesso e marcar synced igual a 201.
    await setupTest(page, context, baseURL, {
      parteResponses: [{ kind: "success", status: 200 }],
      recordedRequests: recorded,
    });

    await context.setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event("offline")));
    await fillForm(page, { obra: "OBR-IDEMP" });
    await page.getByRole("button", { name: SUBMIT_BUTTON_RE }).click();
    await expect(
      page.getByText(/1 parte\(s\) aguardando sincronizar/i),
    ).toBeVisible();

    const itemsBefore = await readQueue(page);
    expect(itemsBefore).toHaveLength(1);
    const uuidEnviado = itemsBefore[0]!.client_uuid;

    await context.setOffline(false);

    await expect(
      page.getByText(/parte\(s\) aguardando sincronizar/i),
    ).toHaveCount(0, { timeout: 10_000 });
    await expect(
      page.getByText(/sincronizadas 1 parte\(s\)/i),
    ).toBeVisible();

    expect(await queueLength(page)).toBe(0);
    expect(recorded).toHaveLength(1);
    // O drain reusa o mesmo client_uuid -- backend identifica como
    // duplicata e devolve 200, sem cria duplicado.
    expect(recorded[0]!.client_uuid).toBe(uuidEnviado);
  });
});
