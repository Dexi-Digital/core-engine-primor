// Service worker do PWA mobile (D5 fase 2).
//
// Estrategia:
// - APP SHELL  -> cache-first (HTML/JS/CSS/icons sob /m/)
// - API        -> network-first com fallback p/ erro 503; o cliente
//                 detecta e enfileira no IndexedDB local (ver
//                 src/lib/pwa-offline-queue.ts).
// - SYNC       -> evento `sync` (Background Sync API) dispara um
//                 postMessage para todas as abas avisando que voltou
//                 conexao -- a aba mais ativa drena a fila.
//
// Versao do cache no nome -- bump manual quando o shell mudar pra
// invalidar caches antigos (sem isso o usuario fica preso na versao
// instalada e o SW antigo serve forever).

const CACHE_VERSION = "mc-pwa-v1";
const SHELL_URLS = [
  "/m/parte-diaria",
  "/manifest.webmanifest",
  "/m/icons/icon-192.svg",
  "/m/icons/icon-512.svg",
];

self.addEventListener("install", (event) => {
  // skipWaiting: assume controle imediatamente em vez de esperar
  // todas as abas serem fechadas. OK pra um app de campo onde so ha
  // 1 aba aberta normalmente.
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL_URLS)),
  );
});

self.addEventListener("activate", (event) => {
  // Drop caches de versoes anteriores.
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

function isApiRequest(url) {
  // Encaminhamos so o que e API do dominio mobile (/m/api/...);
  // o SW NAO intercepta /api/v1/... direto -- o cliente fala com
  // a Next Route Handler /m/api/* que serializa a request e
  // adiciona o cookie de auth automaticamente.
  return url.pathname.startsWith("/m/api/");
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Sempre deixa requests cross-origin passarem direto -- nao temos
  // razao para cache-las (login/health vem do mesmo origin).
  if (url.origin !== self.location.origin) {
    return;
  }

  if (isApiRequest(url)) {
    // Network-first com timeout pequeno -- offline cai pro queue
    // local rapido em vez de esperar 30s do tcp resetting.
    event.respondWith(
      (async () => {
        try {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 4000);
          const res = await fetch(event.request, {
            signal: controller.signal,
          });
          clearTimeout(timer);
          return res;
        } catch {
          // Sinaliza ao client que esta offline -- o caller (form)
          // enfileira no IndexedDB e avisa o usuario.
          return new Response(
            JSON.stringify({
              error: "offline",
              message: "Sem conexao -- request enfileirada localmente.",
            }),
            {
              status: 503,
              headers: { "content-type": "application/json" },
            },
          );
        }
      })(),
    );
    return;
  }

  // App shell -> cache-first.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request)
        .then((res) => {
          // Cacheia GETs do shell sob demanda. POSTs/PUTs nao entram.
          if (event.request.method === "GET" && res.ok) {
            const clone = res.clone();
            caches
              .open(CACHE_VERSION)
              .then((cache) => cache.put(event.request, clone));
          }
          return res;
        })
        .catch(() => {
          // Offline + nao tem em cache -- devolve a propria pagina
          // do form (que ja tem fila offline embutida).
          if (event.request.mode === "navigate") {
            return caches.match("/m/parte-diaria");
          }
          return new Response("offline", { status: 503 });
        });
    }),
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

// Background Sync: drena a fila quando volta a rede. Browsers que
// nao suportam (Safari iOS) caem no path do beforeunload+listener
// `online` no client.
self.addEventListener("sync", (event) => {
  if (event.tag === "drain-parte-diaria-queue") {
    event.waitUntil(
      self.clients
        .matchAll({ includeUncontrolled: true, type: "window" })
        .then((clients) => {
          for (const c of clients) {
            c.postMessage({ type: "DRAIN_QUEUE" });
          }
        }),
    );
  }
});
