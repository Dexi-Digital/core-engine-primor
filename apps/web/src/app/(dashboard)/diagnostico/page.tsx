import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import Link from "next/link";

import { ApiError, apiFetch } from "@/lib/api";

type VersaoServicos = {
  api_fingerprint: string;
  worker_fingerprint: string | null;
  worker_boot_em: string | null;
  em_sincronia: boolean | null;
};

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

type OneDriveSyncRun = {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: string;
  scope: string | null;
  triggered_by: string | null;
  root_folder: string | null;
  files_scanned: number;
  docs_created: number;
  docs_updated: number;
  docs_skipped: number;
  errors_count: number;
  error_message: string | null;
  summary_json: { errors?: Array<{ path: string; reason: string }> } | null;
};

// Relatorio de % de atendimento (demanda #1). Regra na API:
// (ok + vencendo) / total exigido -- vencendo atende mas esta em alerta.
type AtendimentoContagem = {
  total: number;
  ok: number;
  vencendo: number;
  vencido: number;
  ausente: number;
  atendidos: number;
  pendentes: number;
  pct_atendimento: number | null;
  pct_em_dia: number | null;
};

type AtendimentoReport = {
  run_id: number;
  criterio: string;
  geral: AtendimentoContagem;
  por_area: Array<AtendimentoContagem & { area: string }>;
  por_tipo_entidade: Array<AtendimentoContagem & { entity_type: string }>;
  por_entidade: Array<
    AtendimentoContagem & {
      entity_type: string;
      entity_id: number | null;
      entity_label: string;
      areas: string[];
    }
  >;
};

const ENTITY_LABELS: Record<string, string> = {
  employee: "Funcionários",
  veiculo: "Veículos",
  obra: "Obras",
  empresa: "Empresas (CNPJ)",
};

const ENTIDADES_NA_TELA = 15;

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${v.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
}

function pctTone(v: number | null): string {
  if (v === null) return "text-slate-500";
  if (v >= 90) return "text-emerald-700";
  if (v >= 70) return "text-amber-700";
  return "text-red-700";
}

async function fetchAtendimento(runId: number): Promise<AtendimentoReport | null> {
  try {
    return await apiFetch<AtendimentoReport>(
      `/api/v1/diagnostico/runs/${runId}/atendimento`,
    );
  } catch {
    return null;
  }
}

async function fetchRuns(): Promise<DiagnosticoRun[]> {
  try {
    return await apiFetch<DiagnosticoRun[]>("/api/v1/diagnostico/runs?limit=20");
  } catch {
    return [];
  }
}

async function fetchOneDriveRuns(): Promise<OneDriveSyncRun[]> {
  try {
    return await apiFetch<OneDriveSyncRun[]>(
      "/api/v1/onedrive-sync/runs?limit=5",
    );
  } catch {
    return [];
  }
}

async function triggerRun(formData: FormData): Promise<void> {
  "use server";
  const scope = String(formData.get("scope") ?? "all");
  // O run e SINCRONO na API (aceitavel no volume atual). O que faltava
  // era dizer o que aconteceu: erro engolido = "cliquei e nada mudou".
  let erro: string | null = null;
  try {
    await apiFetch("/api/v1/diagnostico/run", {
      method: "POST",
      body: JSON.stringify({ scope }),
    });
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    try {
      erro = (JSON.parse(e.body) as { detail?: string }).detail ?? e.body;
    } catch {
      erro = e.body || `HTTP ${e.status}`;
    }
  }
  revalidatePath("/diagnostico");
  redirect(erro ? `/diagnostico?erro=${encodeURIComponent(erro)}` : "/diagnostico?ok=1");
}

async function triggerOneDriveSync(formData: FormData): Promise<void> {
  "use server";
  const scope = String(formData.get("scope") ?? "all");
  await apiFetch("/api/v1/onedrive-sync/run", {
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

async function fetchVersao(): Promise<VersaoServicos | null> {
  try {
    return await apiFetch<VersaoServicos>("/api/v1/observability/versao");
  } catch {
    // Diagnostico de infraestrutura nao pode derrubar a pagina.
    return null;
  }
}

export default async function DiagnosticoPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await props.searchParams;
  const erroAcao = typeof sp.erro === "string" ? sp.erro : "";
  const okAcao = sp.ok === "1";
  const [runs, oneDriveRuns, versao] = await Promise.all([
    fetchRuns(),
    fetchOneDriveRuns(),
    fetchVersao(),
  ]);
  const lastRun = runs[0];
  const lastOneDriveRun = oneDriveRuns[0];
  const atendimento =
    lastRun && lastRun.status === "done"
      ? await fetchAtendimento(lastRun.id)
      : null;

  return (
    <div className="flex flex-col gap-8">
      {erroAcao && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">O diagnóstico não rodou.</p>
          <p className="mt-1 font-mono text-xs">{erroAcao}</p>
        </div>
      )}
      {okAcao && lastRun && (
        <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-900">
          Diagnóstico executado às {formatDateTime(lastRun.finished_at ?? lastRun.started_at)} —{" "}
          {lastRun.total_findings} verificação(ões), {pctConformidade(lastRun)}% em dia.
        </div>
      )}
      {versao && versao.em_sincronia === false ? (
        <div className="rounded-lg border border-rose-300 bg-rose-50 p-4 text-sm">
          <p className="font-semibold text-rose-900">
            API e worker estão em versões diferentes
          </p>
          <p className="mt-1 text-rose-800">
            Os dois rodam o mesmo código-fonte, então um deles não foi
            reimplantado. Não causa erro — causa comportamento diferente
            (regras e cálculos divergentes entre a tela e as rotinas
            automáticas). Reimplante o serviço defasado.
          </p>
          <p className="mt-2 font-mono text-xs text-rose-700">
            API {versao.api_fingerprint} · worker{" "}
            {versao.worker_fingerprint ?? "—"}
          </p>
        </div>
      ) : null}
      {versao && versao.em_sincronia === null ? (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm">
          <p className="font-semibold text-amber-900">
            O worker nunca registrou boot
          </p>
          <p className="mt-1 text-amber-800">
            Sem isso não dá para saber se ele está na mesma versão da API — e
            as rotinas automáticas (crons) podem não estar rodando.
          </p>
        </div>
      ) : null}
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
              Run #{lastRun.id} — {pctConformidade(lastRun)}% em dia
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
                  <th className="py-2 text-right">% em dia</th>
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

      {lastRun && atendimento && (
        <section
          className="rounded-lg border border-slate-200 bg-white p-6"
          data-testid="atendimento-card"
        >
          <header className="mb-4 flex items-baseline justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold">
                Atendimento documental — {fmtPct(atendimento.geral.pct_atendimento)}
              </h2>
              <p className="mt-1 text-xs text-slate-600">
                % de documentos exigidos que estão válidos hoje (run #
                {atendimento.run_id}). <strong>Vencendo</strong> conta como
                atendido, mas em alerta; <strong>vencido</strong> e{" "}
                <strong>ausente</strong> não atendem. &quot;% em dia&quot;
                desconsidera também os que estão vencendo.
              </p>
            </div>
            <a
              href={`/api/v1/diagnostico/runs/${atendimento.run_id}/atendimento.csv`}
              className="shrink-0 rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50"
            >
              Exportar relatório (CSV)
            </a>
          </header>

          <div className="grid gap-6 lg:grid-cols-2">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="py-2">Área / perfil</th>
                  <th className="py-2 text-right">Exigidos</th>
                  <th className="py-2 text-right">Atendidos</th>
                  <th className="py-2 text-right">% atend.</th>
                  <th className="py-2 text-right">% em dia</th>
                </tr>
              </thead>
              <tbody>
                {atendimento.por_area.map((a) => (
                  <tr key={a.area} className="border-b border-slate-100">
                    <td className="py-2 font-medium">{AREA_LABELS[a.area] ?? a.area}</td>
                    <td className="py-2 text-right">{a.total}</td>
                    <td className="py-2 text-right">{a.atendidos}</td>
                    <td className={`py-2 text-right font-semibold ${pctTone(a.pct_atendimento)}`}>
                      {fmtPct(a.pct_atendimento)}
                    </td>
                    <td className="py-2 text-right text-slate-600">{fmtPct(a.pct_em_dia)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="py-2">Tipo de entidade</th>
                  <th className="py-2 text-right">Exigidos</th>
                  <th className="py-2 text-right">Atendidos</th>
                  <th className="py-2 text-right">% atend.</th>
                  <th className="py-2 text-right">% em dia</th>
                </tr>
              </thead>
              <tbody>
                {atendimento.por_tipo_entidade.map((t) => (
                  <tr key={t.entity_type} className="border-b border-slate-100">
                    <td className="py-2 font-medium">
                      {ENTITY_LABELS[t.entity_type] ?? t.entity_type}
                    </td>
                    <td className="py-2 text-right">{t.total}</td>
                    <td className="py-2 text-right">{t.atendidos}</td>
                    <td className={`py-2 text-right font-semibold ${pctTone(t.pct_atendimento)}`}>
                      {fmtPct(t.pct_atendimento)}
                    </td>
                    <td className="py-2 text-right text-slate-600">{fmtPct(t.pct_em_dia)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {atendimento.por_entidade.length > 0 && (
            <div className="mt-6">
              <h3 className="text-sm font-semibold">
                Menor atendimento por entidade
                <span className="ml-2 text-xs font-normal text-slate-500">
                  {Math.min(ENTIDADES_NA_TELA, atendimento.por_entidade.length)} de{" "}
                  {atendimento.por_entidade.length} — lista completa no CSV
                </span>
              </h3>
              <table className="mt-2 w-full text-sm">
                <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="py-2">Entidade</th>
                    <th className="py-2">Tipo</th>
                    <th className="py-2 text-right">Vencido</th>
                    <th className="py-2 text-right">Ausente</th>
                    <th className="py-2 text-right">Vencendo</th>
                    <th className="py-2 text-right">% atend.</th>
                  </tr>
                </thead>
                <tbody>
                  {atendimento.por_entidade.slice(0, ENTIDADES_NA_TELA).map((e) => (
                    <tr
                      key={`${e.entity_type}-${e.entity_id ?? e.entity_label}`}
                      className="border-b border-slate-100"
                    >
                      <td className="max-w-[320px] truncate py-2" title={e.entity_label}>
                        {e.entity_label}
                      </td>
                      <td className="py-2 text-xs text-slate-600">
                        {ENTITY_LABELS[e.entity_type] ?? e.entity_type}
                      </td>
                      <td className="py-2 text-right text-red-700">{e.vencido}</td>
                      <td className="py-2 text-right text-slate-700">{e.ausente}</td>
                      <td className="py-2 text-right text-amber-700">{e.vencendo}</td>
                      <td className={`py-2 text-right font-semibold ${pctTone(e.pct_atendimento)}`}>
                        {fmtPct(e.pct_atendimento)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <section
        className="rounded-lg border border-slate-200 bg-white p-6"
        data-testid="onedrive-sync-card"
      >
        <header className="mb-3 flex items-baseline justify-between">
          <div>
            <h2 className="text-lg font-semibold">Sincronizar OneDrive</h2>
            <p className="mt-1 text-xs text-slate-600">
              Varre a pasta raiz do OneDrive e importa documentos
              seguindo a convencao <code>dp/&#123;id&#125;/TIPO.pdf</code>,{" "}
              <code>frota/&#123;placa&#125;/TIPO.pdf</code>,{" "}
              <code>obras/&#123;codigo&#125;/TIPO.pdf</code>,{" "}
              <code>empresa/TIPO.pdf</code>. Cada run e idempotente —
              rodar duas vezes nao duplica documentos.{" "}
              <Link
                href="/diagnostico/onedrive"
                className="font-medium text-sky-700 underline hover:text-sky-900"
              >
                Ver diagnóstico de estrutura →
              </Link>
            </p>
          </div>
          <form action={triggerOneDriveSync} className="flex items-end gap-2">
            <label className="flex flex-col text-xs">
              <span className="mb-1 font-medium text-slate-700">Escopo</span>
              <select
                name="scope"
                defaultValue="all"
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                <option value="all">Todas as pastas</option>
                <option value="dp">DP</option>
                <option value="frota">Frota</option>
                <option value="obras">Obras</option>
                <option value="empresa">Empresa</option>
              </select>
            </label>
            <button
              type="submit"
              className="rounded-md bg-sky-700 px-4 py-2 text-sm font-medium text-white hover:bg-sky-800"
            >
              Sincronizar agora
            </button>
          </form>
        </header>

        {lastOneDriveRun ? (
          <div className="grid grid-cols-5 gap-3 text-sm">
            <div className="rounded-md bg-slate-100 p-3">
              <p className="text-xs text-slate-700">Arquivos</p>
              <p className="text-xl font-semibold text-slate-900">
                {lastOneDriveRun.files_scanned}
              </p>
            </div>
            <div className="rounded-md bg-emerald-50 p-3">
              <p className="text-xs text-emerald-700">Criados</p>
              <p className="text-xl font-semibold text-emerald-900">
                {lastOneDriveRun.docs_created}
              </p>
            </div>
            <div className="rounded-md bg-sky-50 p-3">
              <p className="text-xs text-sky-700">Atualizados</p>
              <p className="text-xl font-semibold text-sky-900">
                {lastOneDriveRun.docs_updated}
              </p>
            </div>
            <div className="rounded-md bg-slate-50 p-3">
              <p className="text-xs text-slate-600">Ignorados</p>
              <p className="text-xl font-semibold text-slate-700">
                {lastOneDriveRun.docs_skipped}
              </p>
            </div>
            <div className="rounded-md bg-red-50 p-3">
              <p className="text-xs text-red-700">Erros</p>
              <p className="text-xl font-semibold text-red-900">
                {lastOneDriveRun.errors_count}
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            Nenhuma sincronizacao executada ainda.
          </p>
        )}

        {lastOneDriveRun?.error_message && (
          <p className="mt-3 rounded-md bg-red-50 p-3 text-xs text-red-800">
            <strong>Erro:</strong> {lastOneDriveRun.error_message}
          </p>
        )}

        {oneDriveRuns.length > 1 && (
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer text-xs text-slate-600">
              Historico ({oneDriveRuns.length} ultimos runs)
            </summary>
            <table className="mt-3 w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="py-2">#</th>
                  <th className="py-2">Iniciado</th>
                  <th className="py-2">Escopo</th>
                  <th className="py-2">Por</th>
                  <th className="py-2 text-right">Arquivos</th>
                  <th className="py-2 text-right">Criados</th>
                  <th className="py-2 text-right">Atualizados</th>
                  <th className="py-2 text-right">Erros</th>
                  <th className="py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {oneDriveRuns.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100">
                    <td className="py-2 font-mono">{r.id}</td>
                    <td className="py-2">{formatDateTime(r.started_at)}</td>
                    <td className="py-2">{r.scope ?? "all"}</td>
                    <td className="py-2 text-xs text-slate-600">
                      {r.triggered_by ?? "system"}
                    </td>
                    <td className="py-2 text-right">{r.files_scanned}</td>
                    <td className="py-2 text-right text-emerald-700">
                      {r.docs_created}
                    </td>
                    <td className="py-2 text-right text-sky-700">
                      {r.docs_updated}
                    </td>
                    <td className="py-2 text-right text-red-700">
                      {r.errors_count}
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
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </section>

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
                <th className="py-2 text-right">% em dia</th>
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
