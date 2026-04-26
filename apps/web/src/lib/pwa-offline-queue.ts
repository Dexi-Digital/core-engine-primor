/**
 * Fila offline de partes diarias (D5 fase 2).
 *
 * Guarda submissoes do form quando a rede esta indisponivel
 * (`navigator.onLine === false` ou response 503 do SW). Sincroniza
 * quando voltar conexao -- via evento `online` do window e via
 * `sync` do Background Sync API (se suportado).
 *
 * Schema do object store `pending`:
 *   - `client_uuid` (string, keyPath): UUID gerado no cliente; mesma
 *     chave que o backend usa para idempotencia. Se o sync reenviar a
 *     mesma parte 2x (ex: usuario voltou online com 2 abas), o
 *     backend devolve a row existente em vez de duplicar.
 *   - `payload`: body JSON da submissao, conforme
 *     `ParteDiariaManualCreate` no backend.
 *   - `enqueued_at`: ISO timestamp -- ordena a fila do mais antigo
 *     para o mais novo.
 *   - `last_error`: ultima mensagem de erro de sync, util pra UI
 *     mostrar "essa parte falhou 3x" e nao retentar pra sempre.
 *   - `attempts`: contador para backoff/desistencia eventual.
 */

const DB_NAME = "mc-pwa-pd";
const DB_VERSION = 1;
const STORE = "pending";

export type PartePayload = {
  data?: string | null;
  veiculo_id?: number | null;
  operador?: string | null;
  obra?: string | null;
  equipamento?: string | null;
  placa?: string | null;
  horimetro_inicio?: string | null;
  horimetro_fim?: string | null;
  km_inicio?: number | null;
  km_fim?: number | null;
  combustivel_litros?: string | null;
  combustivel_custo?: string | null;
  observacoes?: string | null;
};

export type QueuedParte = {
  client_uuid: string;
  payload: PartePayload;
  enqueued_at: string;
  attempts: number;
  last_error: string | null;
};

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "client_uuid" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function genUuid(): string {
  // crypto.randomUUID disponivel em todos navegadores que rodam SW
  // (Chrome 92+, Safari 15+, Firefox 95+). Fallback nao precisa.
  return crypto.randomUUID();
}

export async function enqueueParte(
  payload: PartePayload,
): Promise<QueuedParte> {
  const item: QueuedParte = {
    client_uuid: genUuid(),
    payload: { ...payload },
    enqueued_at: new Date().toISOString(),
    attempts: 0,
    last_error: null,
  };
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).add(item);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
  return item;
}

export async function listPending(): Promise<QueuedParte[]> {
  const db = await openDb();
  const items = await new Promise<QueuedParte[]>((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result as QueuedParte[]);
    req.onerror = () => reject(req.error);
  });
  db.close();
  // Mais antigos primeiro -- garantia FIFO no sync.
  return items.sort((a, b) => a.enqueued_at.localeCompare(b.enqueued_at));
}

export async function markSynced(client_uuid: string): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(client_uuid);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function markFailed(
  client_uuid: string,
  error_msg: string,
): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const getReq = store.get(client_uuid);
    getReq.onsuccess = () => {
      const item = getReq.result as QueuedParte | undefined;
      if (!item) {
        resolve();
        return;
      }
      item.attempts += 1;
      item.last_error = error_msg.slice(0, 500);
      store.put(item);
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function clearAll(): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export type SyncResult = {
  total: number;
  ok: number;
  failed: number;
};

/**
 * Drena a fila enviando cada item para o endpoint do PWA. Cada item
 * vira POST /m/api/parte-diaria com `client_uuid` no body para
 * idempotencia. Items que ja foram aceitos (200 ou 201) saem do
 * IndexedDB; falhas ficam para retry no proximo ciclo.
 *
 * Falhas nao-fatais (4xx que indicam payload errado) tambem ficam --
 * o usuario precisa abrir a UI e ver o erro pra corrigir manualmente.
 */
export async function drainQueue(): Promise<SyncResult> {
  const items = await listPending();
  let ok = 0;
  let failed = 0;
  for (const item of items) {
    try {
      const res = await fetch("/m/api/parte-diaria", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...item.payload,
          client_uuid: item.client_uuid,
        }),
      });
      if (res.status === 200 || res.status === 201) {
        await markSynced(item.client_uuid);
        ok += 1;
      } else {
        const text = await res.text();
        await markFailed(item.client_uuid, `${res.status}: ${text}`);
        failed += 1;
      }
    } catch (err) {
      await markFailed(
        item.client_uuid,
        err instanceof Error ? err.message : String(err),
      );
      failed += 1;
    }
  }
  return { total: items.length, ok, failed };
}
