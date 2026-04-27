import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config -- E2E do PWA `/m/parte-diaria` (PR #29).
 *
 * Estrategia:
 *   - `webServer: next dev` sobe o frontend, todos os tests rodam
 *     contra http://localhost:3100 (porta diferente da padrao 3000 pra
 *     nao colidir com `next dev` que o usuario possa estar rodando).
 *   - Nao subimos FastAPI -- todos os specs mockam `/m/api/*` via
 *     `page.route()`. Foco e validar form/queue/SW behavior, nao
 *     chamar a API real.
 *   - Mobile viewport (`Pixel 5`) refletindo o uso real (apontador
 *     em obra com celular).
 *   - `reuseExistingServer` em dev pra iteracao rapida.
 */
const PORT = Number(process.env.PLAYWRIGHT_PORT ?? 3100);

export default defineConfig({
  testDir: "./tests/e2e",
  // Opcional pra CI -- pinga em paralelo se a maquina aguenta. Localmente
  // 1 worker mantem deterministico (IndexedDB e por contexto, mas SW e
  // global por origem -- 2 testes simultaneos podem disputar).
  workers: process.env.CI ? 2 : 1,
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    video: "retain-on-failure",
    screenshot: "only-on-failure",
    // Bloqueia o registro do SW. Sem isso, o `/sw.js` da pagina
    // intercepta /m/api/* com fetch interno do worker -- e essas
    // requests do SW NAO passam pelo `page.route()` (Playwright
    // so intercepta requests do contexto principal). Resultado:
    // mock nao recebia hits, asserts em recordedRequests falhavam
    // e o SW devolvia 503 do catch (offline simulado).
    //
    // O form tolera SW indisponivel (catch silencioso na
    // registration); a fila offline continua funcionando via
    // IndexedDB direto, que e o que estamos testando.
    serviceWorkers: "block",
  },
  projects: [
    {
      name: "chromium-mobile",
      use: { ...devices["Pixel 5"] },
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${PORT}`,
    url: `http://localhost:${PORT}/m/parte-diaria`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
    env: {
      // Manda /m/api/parte-diaria proxiar pra um endpoint inexistente -- nao
      // importa porque o teste mocka a rota. Se algum spec esquecer de mockar,
      // o fetch falha cedo (porta 9 e reservada e nao escuta) em vez de bater
      // num backend real e poluir.
      NEXT_PUBLIC_API_BASE_URL: "http://localhost:9",
    },
  },
});
