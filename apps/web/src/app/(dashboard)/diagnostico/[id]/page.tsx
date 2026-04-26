import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiError, apiFetch } from "@/lib/api";

type DiagnosticoFinding = {
  id: number;
  area: string;
  entity_type: string;
  entity_id: number | null;
  entity_label: string;
  doc_tipo: string;
  doc_label: string | null;
  status: string;
  validade: string | null;
  dias_para_vencimento: number | null;
  message: string | null;
};

type DiagnosticoRunDetail = {
  run: {
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
  };
  findings_by_area: Record<string, DiagnosticoFinding[]>;
};

const AREA_LABELS: Record<string, string> = {
  dp: "DP",
  sst: "SST",
  frota: "Frota",
  empresa: "Empresa",
  obra: "Obras",
};

const STATUS_BADGE: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-700",
  vencendo: "bg-amber-100 text-amber-700",
  vencido: "bg-red-100 text-red-700",
  ausente: "bg-slate-200 text-slate-700",
};

export const dynamic = "force-dynamic";

function formatDate(s: string | null): string {
  if (!s) return "—";
  const [y, m, d] = s.split("-");
  return `${d}/${m}/${y}`;
}

export default async function DiagnosticoDetail({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ status?: string; area?: string }>;
}) {
  const { id } = await params;
  const filters = await searchParams;
  let detail: DiagnosticoRunDetail;
  try {
    detail = await apiFetch<DiagnosticoRunDetail>(
      `/api/v1/diagnostico/runs/${id}`,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const allAreas = Object.keys(detail.findings_by_area);
  const filterArea = filters.area;
  const filterStatus = filters.status;
  const visibleAreas = filterArea
    ? allAreas.filter((a) => a === filterArea)
    : allAreas;

  return (
    <div className="flex flex-col gap-6">
      <header>
        <Link
          href="/diagnostico"
          className="text-xs text-slate-500 hover:underline"
        >
          ← Voltar
        </Link>
        <h1 className="mt-2 text-2xl font-semibold">
          Diagnóstico Run #{detail.run.id}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          {detail.run.total_findings} findings · OK {detail.run.ok_count} ·
          Vencendo {detail.run.vencendo_count} · Vencido{" "}
          {detail.run.vencido_count} · Ausente {detail.run.ausente_count}
        </p>
      </header>

      <nav className="flex flex-wrap gap-2 text-xs">
        <Link
          href={`/diagnostico/${id}`}
          className={`rounded-full border px-3 py-1 ${
            !filterArea && !filterStatus
              ? "border-slate-900 bg-slate-900 text-white"
              : "border-slate-300 hover:bg-slate-50"
          }`}
        >
          Tudo
        </Link>
        {["ausente", "vencido", "vencendo", "ok"].map((s) => (
          <Link
            key={s}
            href={`/diagnostico/${id}?status=${s}`}
            className={`rounded-full border px-3 py-1 ${
              filterStatus === s
                ? "border-slate-900 bg-slate-900 text-white"
                : "border-slate-300 hover:bg-slate-50"
            }`}
          >
            {s}
          </Link>
        ))}
        <span className="mx-2 text-slate-300">|</span>
        {allAreas.map((a) => (
          <Link
            key={a}
            href={`/diagnostico/${id}?area=${a}`}
            className={`rounded-full border px-3 py-1 ${
              filterArea === a
                ? "border-slate-900 bg-slate-900 text-white"
                : "border-slate-300 hover:bg-slate-50"
            }`}
          >
            {AREA_LABELS[a] ?? a}
          </Link>
        ))}
        <a
          href={`/api/v1/diagnostico/runs/${id}/export.csv`}
          className="ml-auto rounded-md border border-slate-300 px-3 py-1 hover:bg-slate-50"
        >
          Exportar CSV
        </a>
      </nav>

      {visibleAreas.length === 0 ? (
        <p className="text-sm text-slate-500">Sem findings para esse filtro.</p>
      ) : (
        visibleAreas.map((area) => {
          const rows = detail.findings_by_area[area] ?? [];
          const filtered = filterStatus
            ? rows.filter((f) => f.status === filterStatus)
            : rows;
          if (filtered.length === 0) return null;
          return (
            <section
              key={area}
              className="rounded-lg border border-slate-200 bg-white p-4"
            >
              <h2 className="mb-3 text-base font-semibold">
                {AREA_LABELS[area] ?? area}{" "}
                <span className="text-xs font-normal text-slate-500">
                  ({filtered.length})
                </span>
              </h2>
              <table className="w-full text-sm">
                <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="py-2">Entidade</th>
                    <th className="py-2">Documento</th>
                    <th className="py-2">Status</th>
                    <th className="py-2">Validade</th>
                    <th className="py-2 text-right">Dias</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((f) => (
                    <tr key={f.id} className="border-b border-slate-100">
                      <td className="py-2 text-slate-700">{f.entity_label}</td>
                      <td className="py-2 text-slate-700">
                        {f.doc_label ?? f.doc_tipo}
                      </td>
                      <td className="py-2">
                        <span
                          className={`rounded px-2 py-0.5 text-xs ${
                            STATUS_BADGE[f.status] ?? ""
                          }`}
                        >
                          {f.status}
                        </span>
                      </td>
                      <td className="py-2 text-slate-700">
                        {formatDate(f.validade)}
                      </td>
                      <td className="py-2 text-right text-slate-700">
                        {f.dias_para_vencimento ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          );
        })
      )}
    </div>
  );
}
