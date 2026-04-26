"use client";

/**
 * Form mobile (D5 fase 2) para o apontador em campo. Estrategia
 * offline-first:
 *
 * 1. Submissao tenta POST /m/api/parte-diaria.
 * 2. Se navegador esta offline OU response e 503 ("offline" do SW),
 *    grava no IndexedDB local via `enqueueParte`.
 * 3. Quando volta a rede (`window.online` ou `DRAIN_QUEUE` do SW),
 *    `drainQueue` reenvia tudo na ordem -- backend devolve 200 nas
 *    ja persistidas (idempotencia via client_uuid).
 * 4. UI mostra a fila pendente em uma lista no topo, com contador.
 *
 * Nao usamos React Query / SWR aqui -- o form e simples e o
 * estado offline e sem cache. Manter dependencias zero ajuda no
 * tamanho do bundle (importante pra app de campo em 3G).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  drainQueue,
  enqueueParte,
  listPending,
  newClientUuid,
  type PartePayload,
  type QueuedParte,
} from "@/lib/pwa-offline-queue";

type Veiculo = { id: number; placa: string; descricao: string };
type FormState = {
  veiculo_id: string;
  data: string;
  operador: string;
  obra: string;
  horimetro_inicio: string;
  horimetro_fim: string;
  km_inicio: string;
  km_fim: string;
  combustivel_litros: string;
  combustivel_custo: string;
  observacoes: string;
};

const EMPTY: FormState = {
  veiculo_id: "",
  data: new Date().toISOString().slice(0, 10),
  operador: "",
  obra: "",
  horimetro_inicio: "",
  horimetro_fim: "",
  km_inicio: "",
  km_fim: "",
  combustivel_litros: "",
  combustivel_custo: "",
  observacoes: "",
};

function toPayload(form: FormState): PartePayload {
  return {
    veiculo_id: form.veiculo_id ? Number(form.veiculo_id) : null,
    data: form.data || null,
    operador: form.operador.trim() || null,
    obra: form.obra.trim() || null,
    horimetro_inicio: form.horimetro_inicio || null,
    horimetro_fim: form.horimetro_fim || null,
    km_inicio: form.km_inicio ? Number(form.km_inicio) : null,
    km_fim: form.km_fim ? Number(form.km_fim) : null,
    combustivel_litros: form.combustivel_litros || null,
    combustivel_custo: form.combustivel_custo || null,
    observacoes: form.observacoes.trim() || null,
  };
}

export default function ParteDiariaMobileForm() {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [veiculos, setVeiculos] = useState<Veiculo[]>([]);
  const [pending, setPending] = useState<QueuedParte[]>([]);
  const [online, setOnline] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<{
    kind: "ok" | "queued" | "error";
    msg: string;
  } | null>(null);
  const swRegistered = useRef(false);

  const refreshPending = useCallback(async () => {
    try {
      const items = await listPending();
      setPending(items);
    } catch {
      // IndexedDB indisponivel (modo privado / cookies bloqueados).
      // Nao crasheamos a UI -- offline so deixa de funcionar.
      setPending([]);
    }
  }, []);

  const sync = useCallback(async () => {
    if (!navigator.onLine) return;
    try {
      const r = await drainQueue();
      if (r.ok > 0) {
        setFeedback({
          kind: "ok",
          msg: `Sincronizadas ${r.ok} parte(s).`,
        });
      }
      await refreshPending();
    } catch {
      // sync best-effort -- deixa silencioso, vai retentar.
    }
  }, [refreshPending]);

  // Registra SW + carrega veiculos + ouve online/offline + drena fila.
  useEffect(() => {
    setOnline(navigator.onLine);

    if (
      typeof navigator !== "undefined" &&
      "serviceWorker" in navigator &&
      !swRegistered.current
    ) {
      swRegistered.current = true;
      navigator.serviceWorker
        .register("/sw.js", { scope: "/m/" })
        .catch(() => {
          // SW desativado em dev / iframe -- form continua funcional
          // online; offline-queue ainda funciona via IndexedDB.
        });
      navigator.serviceWorker.addEventListener("message", (ev) => {
        if (ev.data && ev.data.type === "DRAIN_QUEUE") {
          void sync();
        }
      });
    }

    const onOnline = () => {
      setOnline(true);
      void sync();
    };
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);

    void refreshPending();

    // Carrega veiculos para o dropdown -- se offline, ignora; o
    // apontador pode digitar a placa manualmente no campo `obra`
    // ou completar depois quando reconectar.
    void fetch("/m/api/veiculos")
      .then(async (r) => {
        if (!r.ok) return;
        const data = (await r.json()) as { items: Veiculo[] };
        setVeiculos(data.items);
      })
      .catch(() => {
        /* offline na primeira carga -- usuario digita manualmente. */
      });

    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, [refreshPending, sync]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setFeedback(null);
    const payload = toPayload(form);
    // UUID gerado UMA VEZ por submit -- mesmo valor enviado online e
    // gravado na fila offline em caso de fallback. Sem isso, se a
    // request online tem o response perdido (rede de obra) e cai no
    // catch, o enqueue gerava UUID novo e o retry duplicava no
    // backend (que so dedup pelo UUID original).
    const client_uuid = newClientUuid();
    const bodyWithUuid = { ...payload, client_uuid };

    if (!navigator.onLine) {
      // Offline: grava no IndexedDB direto sem nem tentar fetch.
      await enqueueParte(payload, client_uuid);
      setFeedback({
        kind: "queued",
        msg: "Sem sinal -- parte salva localmente. Sincroniza quando voltar.",
      });
      setForm({ ...EMPTY, data: form.data, obra: form.obra });
      await refreshPending();
      // Pede ao SW pra agendar sync quando voltar a rede.
      try {
        const reg = await navigator.serviceWorker.ready;
        const swReg = reg as ServiceWorkerRegistration & {
          sync?: { register: (tag: string) => Promise<void> };
        };
        if (swReg.sync) {
          await swReg.sync.register("drain-parte-diaria-queue");
        }
      } catch {
        /* Background Sync nao suportado (Safari) -- caimos no
           listener de window.online. */
      }
      setSubmitting(false);
      return;
    }

    try {
      const res = await fetch("/m/api/parte-diaria", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(bodyWithUuid),
      });
      if (res.status === 503) {
        // SW devolveu 503 (network falhou apesar do navigator.onLine).
        await enqueueParte(payload, client_uuid);
        setFeedback({
          kind: "queued",
          msg: "Sem conexao -- parte salva localmente.",
        });
        setForm({ ...EMPTY, data: form.data, obra: form.obra });
        await refreshPending();
      } else if (res.ok) {
        setFeedback({ kind: "ok", msg: "Parte salva." });
        setForm({ ...EMPTY, data: form.data, obra: form.obra });
      } else {
        const text = await res.text();
        setFeedback({ kind: "error", msg: `Erro ${res.status}: ${text}` });
      }
    } catch {
      // Fetch lancou (DNS, rede caida sem o SW) OU response foi
      // perdido apos o servidor ter aceitado -- nao temos como
      // distinguir do client. Enfileira com o MESMO client_uuid
      // que foi enviado: se o servidor ja persistiu, o retry vai
      // bater no fast-path de idempotencia (200) e ser drenado;
      // se nao persistiu, vira nova row (201). Sem duplicata em
      // nenhum dos casos.
      await enqueueParte(payload, client_uuid);
      setFeedback({
        kind: "queued",
        msg: "Sem sinal -- parte salva localmente.",
      });
      setForm({ ...EMPTY, data: form.data, obra: form.obra });
      await refreshPending();
    }
    setSubmitting(false);
  }

  return (
    <div className="space-y-4">
      <div
        className={`rounded-lg border px-3 py-2 text-xs ${
          online
            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
            : "border-amber-300 bg-amber-50 text-amber-800"
        }`}
        data-testid="conn-indicator"
      >
        {online ? "Online" : "Offline -- entradas vao para fila local"}
      </div>

      {pending.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <p className="font-semibold">
            {pending.length} parte(s) aguardando sincronizar
          </p>
          <button
            type="button"
            onClick={() => void sync()}
            className="mt-2 rounded-md bg-amber-600 px-3 py-1.5 text-white"
            disabled={!online}
          >
            Sincronizar agora
          </button>
        </div>
      )}

      {feedback && (
        <div
          className={`rounded-lg border px-3 py-2 text-sm ${
            feedback.kind === "ok"
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : feedback.kind === "queued"
                ? "border-amber-200 bg-amber-50 text-amber-800"
                : "border-red-200 bg-red-50 text-red-800"
          }`}
        >
          {feedback.msg}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-3" data-testid="pd-form">
        <Field label="Veiculo">
          <select
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
            value={form.veiculo_id}
            onChange={(e) => setForm((f) => ({ ...f, veiculo_id: e.target.value }))}
          >
            <option value="">-- selecione --</option>
            {veiculos.map((v) => (
              <option key={v.id} value={v.id}>
                {v.placa} {v.descricao && `(${v.descricao})`}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Data">
          <input
            type="date"
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
            value={form.data}
            onChange={(e) => setForm((f) => ({ ...f, data: e.target.value }))}
          />
        </Field>

        <Field label="Operador">
          <input
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
            value={form.operador}
            onChange={(e) =>
              setForm((f) => ({ ...f, operador: e.target.value }))
            }
            placeholder="Nome de quem opera"
          />
        </Field>

        <Field label="Obra / Frente">
          <input
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
            value={form.obra}
            onChange={(e) => setForm((f) => ({ ...f, obra: e.target.value }))}
            placeholder="Codigo ou nome da obra"
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Horimetro inicio">
            <input
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
              value={form.horimetro_inicio}
              onChange={(e) =>
                setForm((f) => ({ ...f, horimetro_inicio: e.target.value }))
              }
            />
          </Field>
          <Field label="Horimetro fim">
            <input
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
              value={form.horimetro_fim}
              onChange={(e) =>
                setForm((f) => ({ ...f, horimetro_fim: e.target.value }))
              }
            />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="KM inicio">
            <input
              type="number"
              inputMode="numeric"
              step="1"
              min="0"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
              value={form.km_inicio}
              onChange={(e) =>
                setForm((f) => ({ ...f, km_inicio: e.target.value }))
              }
            />
          </Field>
          <Field label="KM fim">
            <input
              type="number"
              inputMode="numeric"
              step="1"
              min="0"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
              value={form.km_fim}
              onChange={(e) =>
                setForm((f) => ({ ...f, km_fim: e.target.value }))
              }
            />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Combustivel (L)">
            <input
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
              value={form.combustivel_litros}
              onChange={(e) =>
                setForm((f) => ({ ...f, combustivel_litros: e.target.value }))
              }
            />
          </Field>
          <Field label="Custo (R$)">
            <input
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
              value={form.combustivel_custo}
              onChange={(e) =>
                setForm((f) => ({ ...f, combustivel_custo: e.target.value }))
              }
            />
          </Field>
        </div>

        <Field label="Observacoes">
          <textarea
            rows={3}
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base"
            value={form.observacoes}
            onChange={(e) =>
              setForm((f) => ({ ...f, observacoes: e.target.value }))
            }
            placeholder="Observacoes / problemas detectados"
          />
        </Field>

        <button
          type="submit"
          disabled={submitting}
          className="mt-2 w-full rounded-md bg-slate-900 px-3 py-3 text-base font-semibold text-white shadow-sm disabled:opacity-50"
        >
          {submitting ? "Salvando..." : online ? "Enviar" : "Salvar offline"}
        </button>
      </form>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-600">
        {label}
      </span>
      {children}
    </label>
  );
}
