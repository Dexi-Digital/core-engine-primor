import Link from "next/link";

import { apiFetch, ApiError } from "@/lib/api";

type Finding = {
  kind: string;
  area: string;
  entity_key: string | null;
  entity_label: string | null;
  doc_tipo: string | null;
  path: string | null;
  message: string;
};

type EntityReport = {
  area: string;
  entity_key: string;
  label: string;
  ok: boolean;
  findings: Finding[];
};

type DiagnosticoResponse = {
  root_folder: string;
  total_files: number;
  counts: Record<string, number>;
  by_area: Record<string, EntityReport[]>;
};

const AREA_LABELS: Record<string, string> = {
  dp: "DP (funcionários)",
  frota: "Frota (veículos)",
  obras: "Obras",
  empresa: "Empresa (singleton)",
  desconhecido: "Fora do padrão",
};

const FINDING_LABELS: Record<string, string> = {
  faltando: "Faltando",
  extra: "Extra (tipo desconhecido)",
  fora_do_padrao: "Fora do padrão",
  entidade_fantasma: "Entidade fantasma",
  entidade_sem_pasta: "Sem pasta",
  ok: "OK",
};

const FINDING_COLORS: Record<string, string> = {
  faltando: "bg-red-100 text-red-800",
  extra: "bg-amber-100 text-amber-800",
  fora_do_padrao: "bg-violet-100 text-violet-800",
  entidade_fantasma: "bg-pink-100 text-pink-800",
  entidade_sem_pasta: "bg-slate-200 text-slate-800",
  ok: "bg-emerald-100 text-emerald-800",
};

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams: Promise<{ area?: string }>;
};

async function fetchDiagnostico(
  area: string,
): Promise<DiagnosticoResponse | { error: string }> {
  try {
    return await apiFetch<DiagnosticoResponse>(
      `/api/v1/diagnostico/onedrive?area=${encodeURIComponent(area)}`,
    );
  } catch (err) {
    if (err instanceof ApiError) {
      return { error: `API ${err.status}: ${err.body.slice(0, 500)}` };
    }
    return { error: String(err) };
  }
}

export default async function OneDriveDiagnosticoPage(props: PageProps) {
  const sp = await props.searchParams;
  const area = sp.area ?? "all";
  const result = await fetchDiagnostico(area);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">
            Diagnóstico — Estrutura OneDrive
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Compara o conteúdo da pasta raiz do OneDrive/SharePoint com o
            que deveria existir (docs obrigatórios por entidade). Retorna
            lista de diferenças: faltando, extra, fora do padrão, entidade
            fantasma, entidade sem pasta. Executado sob demanda — para
            agendar, use o sync recorrente em{" "}
            <Link
              className="underline"
              href="/diagnostico"
            >
              Diagnóstico Documental
            </Link>
            .
          </p>
        </div>
        <form className="flex items-end gap-2">
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Área</span>
            <select
              name="area"
              defaultValue={area}
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="all">Todas</option>
              <option value="dp">DP</option>
              <option value="frota">Frota</option>
              <option value="obras">Obras</option>
              <option value="empresa">Empresa</option>
            </select>
          </label>
          <button
            type="submit"
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            Rodar
          </button>
        </form>
      </header>

      {"error" in result ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          {result.error}
        </div>
      ) : (
        <>
          <section className="grid grid-cols-2 gap-3 md:grid-cols-6">
            <SummaryCard
              label="Arquivos"
              value={result.total_files}
              tone="slate"
            />
            <SummaryCard
              label="OK"
              value={result.counts.ok ?? 0}
              tone="emerald"
            />
            <SummaryCard
              label="Faltando"
              value={result.counts.faltando ?? 0}
              tone="red"
            />
            <SummaryCard
              label="Extra"
              value={result.counts.extra ?? 0}
              tone="amber"
            />
            <SummaryCard
              label="Fora padrão"
              value={result.counts.fora_do_padrao ?? 0}
              tone="violet"
            />
            <SummaryCard
              label="Sem pasta"
              value={result.counts.entidade_sem_pasta ?? 0}
              tone="slate"
            />
          </section>

          <p className="text-xs text-slate-500">
            Pasta raiz: <code className="font-mono">{result.root_folder || "(não configurada)"}</code>
          </p>

          {Object.entries(result.by_area).map(([areaKey, reports]) => (
            <AreaBlock key={areaKey} area={areaKey} reports={reports} />
          ))}
        </>
      )}
    </div>
  );
}

function SummaryCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "slate" | "emerald" | "red" | "amber" | "violet";
}) {
  const toneClasses: Record<string, string> = {
    slate: "bg-slate-50 text-slate-800",
    emerald: "bg-emerald-50 text-emerald-800",
    red: "bg-red-50 text-red-800",
    amber: "bg-amber-50 text-amber-800",
    violet: "bg-violet-50 text-violet-800",
  };
  return (
    <div className={`rounded-md p-3 ${toneClasses[tone]}`}>
      <p className="text-xs">{label}</p>
      <p className="text-2xl font-semibold">{value}</p>
    </div>
  );
}

function AreaBlock({
  area,
  reports,
}: {
  area: string;
  reports: EntityReport[];
}) {
  if (reports.length === 0) return null;
  const problems = reports.filter((r) => !r.ok);
  const oks = reports.filter((r) => r.ok);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <header className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-semibold">
          {AREA_LABELS[area] ?? area}{" "}
          <span className="text-sm font-normal text-slate-500">
            ({reports.length} entidades)
          </span>
        </h2>
        <span className="text-xs text-slate-500">
          {problems.length} com problemas · {oks.length} ok
        </span>
      </header>

      {problems.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {problems.map((report) => (
            <EntityRow key={`${area}:${report.entity_key}`} report={report} />
          ))}
        </ul>
      )}

      {oks.length > 0 && (
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer text-xs text-slate-500">
            {oks.length} entidade(s) em conformidade
          </summary>
          <ul className="mt-2 divide-y divide-slate-100">
            {oks.map((report) => (
              <li
                key={`${area}:${report.entity_key}`}
                className="flex items-center justify-between py-2 text-sm"
              >
                <span>{report.label}</span>
                <span className="text-xs text-emerald-700">✓ OK</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function EntityRow({ report }: { report: EntityReport }) {
  return (
    <li className="py-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-slate-800">{report.label}</h3>
        <span className="font-mono text-xs text-slate-400">
          {report.entity_key}
        </span>
      </div>
      <ul className="mt-2 space-y-1.5">
        {report.findings.map((f, i) => (
          <li key={i} className="flex items-start gap-2 text-xs">
            <span
              className={`shrink-0 rounded px-2 py-0.5 font-medium ${
                FINDING_COLORS[f.kind] ?? "bg-slate-100 text-slate-700"
              }`}
            >
              {FINDING_LABELS[f.kind] ?? f.kind}
            </span>
            <span className="text-slate-700">
              {f.doc_tipo && (
                <code className="font-mono text-slate-900">
                  {f.doc_tipo}
                </code>
              )}
              {f.doc_tipo && f.path && " — "}
              {f.path && (
                <code className="font-mono text-slate-500">{f.path}</code>
              )}
              {!f.doc_tipo && !f.path && <span>{f.message}</span>}
            </span>
          </li>
        ))}
      </ul>
    </li>
  );
}
