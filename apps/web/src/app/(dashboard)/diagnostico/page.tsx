import { revalidatePath } from "next/cache";
import Link from "next/link";

import { apiFetch } from "@/lib/api";

type DiagnosticoRun = {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: string;
  scope: string | null;
  triggered_by: string | null;
  total_findings: number;
  ok_count: number;
  ausente_count: number;
  vencido_count: number;
  vencendo_count: number;
  summary_json: Record<string, Record<string, number>> | null;
};

const AREA_LABELS: Record<string, string> = {
  dp: "DP (Departamento Pessoal)",
  sst: "SST (Saúde e Segurança)",
  frota: "Frota",
  empresa: "Empresa / Licitação",
  obra: "Obras",
};

const STATUS_BADGE: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-700",
  vencendo: "bg-amber-100 text-amber-700",
  vencido: "bg-red-100 text-red-700",
  ausente: "bg-slate-200 text-slate-700",
};

export const dynamic = "force-dynamic";

async function fetchRuns(): Promise<DiagnosticoRun[]> {
  try {
    return await apiFetch<DiagnosticoRun[]>("/api/v1/diagnostico/runs?limit=20");
  } catch {
    return [];
  }
}

async function triggerRun(formData: FormData): Promise<void> {
  "use server";
  const scope = String(formData.get("scope") ?? "all");
  await apiFetch("/api/v1/diagnostico/run", {
    method: "POST",
    body: JSON.stringify({ scope }),
  });
  revalidatePath("/diagnostico");
}

function formatDateTime(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString("pt-BR");
}

function pctConformidade(run: DiagnosticoRun): number {
  if (run.total_findings === 0) return 100;
  return Math.round((run.ok_count / run.total_findings) * 100);
}

export default async function DiagnosticoPage() {
  const runs = await fetchRuns();
  const lastRun = runs[0];

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Diagnóstico Documental</h1>
          <p className="mt-1 text-sm text-slate-600">
            Auditoria automatizada de documentos por área (DP, SST, Frota,
            Empresa, Obras). Cada run é um snapshot do estado atual do banco
            avaliado contra os checklists regulatórios (CLT, NRs, habilitação
            licitatória).
          </p>
        </div>
        <form action={triggerRun} className="flex items-end gap-2">
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Escopo</span>
            <select
              name="scope"
              defaultValue="all"
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="all">Todas as áreas</option>
              <option value="dp">DP</option>
              <option value="sst">SST</option>
              <option value="frota">Frota</option>
              <option value="empresa">Empresa</option>
              <option value="obra">Obras</option>
            </select>
          </label>
          <button
            type="submit"
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            Rodar diagnóstico
          </button>
        </form>
      </header>

      {lastRun && lastRun.status === "done" && (
        <section className="rounded-lg border border-slate-200 bg-white p-6">
          <header className="mb-4 flex items-baseline justify-between">
            <h2 className="text-lg font-semibold">
              Run #{lastRun.id} — {pctConformidade(lastRun)}% conformidade
            </h2>
            <span className="text-xs text-slate-500">
              {formatDateTime(lastRun.started_at)} ·{" "}
              {lastRun.triggered_by ?? "sistema"}
            </span>
          </header>
          <div className="grid grid-cols-4 gap-3 text-sm">
            <div className="rounded-md bg-emerald-50 p-3">
              <p className="text-xs text-emerald-700">OK</p>
              <p className="text-2xl font-semibold text-emerald-900">
                {lastRun.ok_count}
              </p>
            </div>
            <div className="rounded-md bg-amber-50 p-3">
              <p className="text-xs text-amber-700">Vencendo</p>
              <p className="text-2xl font-semibold text-amber-900">
                {lastRun.vencendo_count}
              </p>
            </div>
            <div className="rounded-md bg-red-50 p-3">
              <p className="text-xs text-red-700">Vencido</p>
              <p className="text-2xl font-semibold text-red-900">
                {lastRun.vencido_count}
              </p>
            </div>
            <div className="rounded-md bg-slate-100 p-3">
              <p className="text-xs text-slate-700">Ausente</p>
              <p className="text-2xl font-semibold text-slate-900">
                {lastRun.ausente_count}
              </p>
            </div>
          </div>

          {lastRun.summary_json && (
            <table className="mt-6 w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="py-2">Área</th>
                  <th className="py-2 text-right">Total</th>
                  <th className="py-2 text-right">OK</th>
                  <th className="py-2 text-right">Vencendo</th>
                  <th className="py-2 text-right">Vencido</th>
                  <th className="py-2 text-right">Ausente</th>
                  <th className="py-2 text-right">% conf.</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(lastRun.summary_json).map(([area, counts]) => {
                  const total = counts.total ?? 0;
                  const ok = counts.ok ?? 0;
                  const pct = total === 0 ? 100 : Math.round((ok / total) * 100);
                  return (
                    <tr key={area} className="border-b border-slate-100">
                      <td className="py-2 font-medium">
                        {AREA_LABELS[area] ?? area}
                      </td>
                      <td className="py-2 text-right">{total}</td>
                      <td className="py-2 text-right text-emerald-700">{ok}</td>
                      <td className="py-2 text-right text-amber-700">
                        {counts.vencendo ?? 0}
                      </td>
                      <td className="py-2 text-right text-red-700">
                        {counts.vencido ?? 0}
                      </td>
                      <td className="py-2 text-right text-slate-700">
                        {counts.ausente ?? 0}
                      </td>
                      <td className="py-2 text-right font-semibold">{pct}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          <div className="mt-4 flex gap-2">
            <Link
              href={`/diagnostico/${lastRun.id}`}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50"
            >
              Ver findings detalhados
            </Link>
            <a
              href={`/api/v1/diagnostico/runs/${lastRun.id}/export.csv`}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50"
            >
              Exportar CSV
            </a>
          </div>
        </section>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="mb-3 text-lg font-semibold">Histórico</h2>
        {runs.length === 0 ? (
          <p className="text-sm text-slate-500">
            Nenhum diagnóstico foi rodado ainda. Clique em &quot;Rodar
            diagnóstico&quot; para começar.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="py-2">#</th>
                <th className="py-2">Iniciado</th>
                <th className="py-2">Escopo</th>
                <th className="py-2">Por</th>
                <th className="py-2 text-right">Findings</th>
                <th className="py-2 text-right">% conf.</th>
                <th className="py-2">Status</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-slate-100">
                  <td className="py-2 font-mono">{r.id}</td>
                  <td className="py-2">{formatDateTime(r.started_at)}</td>
                  <td className="py-2">{r.scope ?? "all"}</td>
                  <td className="py-2 text-xs text-slate-600">
                    {r.triggered_by ?? "system"}
                  </td>
                  <td className="py-2 text-right">{r.total_findings}</td>
                  <td className="py-2 text-right font-semibold">
                    {pctConformidade(r)}%
                  </td>
                  <td className="py-2">
                    <span
                      className={`rounded px-2 py-0.5 text-xs ${
                        STATUS_BADGE[r.status === "done" ? "ok" : "ausente"]
                      }`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="py-2 text-right">
                    <Link
                      href={`/diagnostico/${r.id}`}
                      className="text-xs text-slate-700 underline"
                    >
                      ver
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
