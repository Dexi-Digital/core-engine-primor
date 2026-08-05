import Link from "next/link";

import { apiFetch } from "@/lib/api";

type ConcorrenteRow = {
  cnpj: string;
  razao_social: string | null;
  licitacoes_vencidas: number;
  valor_total_homologado: string | null;
  orgaos_distintos: number;
  ultima_vitoria: string | null;
};

type GeotargetingRow = {
  uf: string | null;
  municipio: string | null;
  licitacoes_com_resultado: number;
  valor_total_homologado: string | null;
};

type NaoCaptados = { total: number; por_status: Record<string, number> };

type Eficiencia = {
  total_triadas: number;
  tempo_medio_triagem_horas: number | null;
  pct_com_planilha: number | null;
  falhas_por_status: Record<string, number>;
};

export const dynamic = "force-dynamic";

async function safeFetch<T>(path: string): Promise<T | null> {
  try {
    return await apiFetch<T>(path);
  } catch {
    return null;
  }
}

function brl(value: string | null): string {
  if (!value) return "—";
  const num = Number(value);
  if (Number.isNaN(num)) return value;
  return num.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

const STATUS_LABELS: Record<string, string> = {
  rejeitado: "Rejeitados",
  sem_planilha: "Sem planilha",
  erro_portal: "Erro de portal",
  erro_sharepoint: "Erro SharePoint",
};

export default async function DashboardsComerciaisPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const searchParams = await props.searchParams;
  const uf = typeof searchParams.uf === "string" && searchParams.uf ? searchParams.uf : "MG";

  const [concorrentes, geo, naoCaptados, eficiencia] = await Promise.all([
    safeFetch<ConcorrenteRow[]>(`/api/v1/licitacoes/dashboards/concorrentes?uf=${uf}`),
    safeFetch<GeotargetingRow[]>(`/api/v1/licitacoes/dashboards/geotargeting?uf=${uf}`),
    safeFetch<NaoCaptados>("/api/v1/licitacoes/dashboards/nao-captados"),
    safeFetch<Eficiencia>("/api/v1/licitacoes/dashboards/eficiencia"),
  ]);

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Inteligência comercial</h1>
          <p className="mt-1 text-sm text-slate-500">
            Concorrentes, geotargeting e eficiência da esteira — dados do PNCP.
          </p>
        </div>
        <Link
          href="/licitacoes"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          ← Licitações
        </Link>
      </header>

      <form className="flex items-end gap-3 rounded-xl border border-slate-200 bg-white p-4">
        <label className="flex flex-col text-xs font-medium text-slate-600">
          UF
          <input
            name="uf"
            defaultValue={uf}
            maxLength={2}
            className="mt-1 w-24 rounded-md border border-slate-300 px-2 py-1 text-sm uppercase"
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
        >
          Filtrar
        </button>
      </form>

      {eficiencia && (
        <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">Licitações triadas</p>
            <p className="mt-1 text-2xl font-bold">{eficiencia.total_triadas}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">Tempo médio até triagem</p>
            <p className="mt-1 text-2xl font-bold">
              {eficiencia.tempo_medio_triagem_horas != null
                ? `${eficiencia.tempo_medio_triagem_horas}h`
                : "—"}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">% com planilha localizada</p>
            <p className="mt-1 text-2xl font-bold">
              {eficiencia.pct_com_planilha != null ? `${eficiencia.pct_com_planilha}%` : "—"}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">Falhas de portal/SharePoint</p>
            <p className="mt-1 text-2xl font-bold">
              {Object.values(eficiencia.falhas_por_status).reduce((a, b) => a + b, 0)}
            </p>
          </div>
        </section>
      )}

      {naoCaptados && naoCaptados.total > 0 && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4">
          <h2 className="text-sm font-semibold text-red-800">
            Não captados ({naoCaptados.total})
          </h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(naoCaptados.por_status).map(([status, count]) => (
              <span
                key={status}
                className="rounded-full bg-white px-3 py-1 text-xs font-medium text-red-700"
              >
                {STATUS_LABELS[status] ?? status}: {count}
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-200 px-4 py-3 text-sm font-semibold">
          Concorrentes por CNPJ ({uf})
        </h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-left text-xs text-slate-500">
              <th className="px-4 py-2">Fornecedor</th>
              <th className="px-4 py-2">CNPJ</th>
              <th className="px-4 py-2 text-right">Vitórias</th>
              <th className="px-4 py-2 text-right">Valor homologado</th>
              <th className="px-4 py-2 text-right">Órgãos</th>
            </tr>
          </thead>
          <tbody>
            {(concorrentes ?? []).map((c) => (
              <tr key={c.cnpj} className="border-b border-slate-50">
                <td className="px-4 py-2 font-medium">{c.razao_social ?? "—"}</td>
                <td className="px-4 py-2 text-slate-500">{c.cnpj}</td>
                <td className="px-4 py-2 text-right">{c.licitacoes_vencidas}</td>
                <td className="px-4 py-2 text-right">{brl(c.valor_total_homologado)}</td>
                <td className="px-4 py-2 text-right">{c.orgaos_distintos}</td>
              </tr>
            ))}
            {(!concorrentes || concorrentes.length === 0) && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-slate-400">
                  Sem resultados ingeridos — rode POST /licitacoes/ingest/resultados.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-200 px-4 py-3 text-sm font-semibold">
          Geotargeting — verba homologada por município ({uf})
        </h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-left text-xs text-slate-500">
              <th className="px-4 py-2">Município</th>
              <th className="px-4 py-2 text-right">Licitações c/ resultado</th>
              <th className="px-4 py-2 text-right">Valor homologado</th>
            </tr>
          </thead>
          <tbody>
            {(geo ?? []).map((g) => (
              <tr key={`${g.uf}-${g.municipio}`} className="border-b border-slate-50">
                <td className="px-4 py-2 font-medium">
                  {g.municipio ?? "—"} <span className="text-slate-400">{g.uf}</span>
                </td>
                <td className="px-4 py-2 text-right">{g.licitacoes_com_resultado}</td>
                <td className="px-4 py-2 text-right">{brl(g.valor_total_homologado)}</td>
              </tr>
            ))}
            {(!geo || geo.length === 0) && (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-slate-400">
                  Sem dados para {uf}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
