import Link from "next/link";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { apiFetch } from "@/lib/api";
import { PageHeader } from "@/components/ui/primitives";

type LicitacaoRead = {
  id: number;
  external_id: string;
  numero_compra: string | null;
  ano_compra: number | null;
  objeto_compra: string | null;
  modalidade_nome: string | null;
  situacao_compra_nome: string | null;
  valor_total_estimado: string | null;
  orgao_razao_social: string | null;
  orgao_cnpj: string | null;
  uf_sigla: string | null;
  municipio_nome: string | null;
  data_publicacao_pncp: string | null;
};

type ListResponse = {
  total: number;
  page: number;
  page_size: number;
  data: LicitacaoRead[];
};

export const dynamic = "force-dynamic";

async function fetchLicitacoes(params: URLSearchParams): Promise<ListResponse | null> {
  try {
    return await apiFetch<ListResponse>(`/api/v1/licitacoes?${params.toString()}`);
  } catch {
    return null;
  }
}

async function downloadEdital(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/edital/download`, { method: "POST" });
  } catch (err) {
    // Keep the page usable even if the download fails; the detail page
    // surfaces the error message from the API response.
    console.error("[licitacoes] edital download failed", err);
  }
  revalidatePath("/licitacoes");
  revalidatePath(`/licitacoes/${id}`);
}

async function iniciarRecargaPncp(formData: FormData): Promise<void> {
  "use server";
  const inicio = String(formData.get("data_inicial") ?? "");
  const fim = String(formData.get("data_final") ?? "");
  const uf = String(formData.get("uf") ?? "").trim().toUpperCase();
  const params = new URLSearchParams({ data_inicial: inicio, data_final: fim });
  if (uf) params.set("uf", uf);
  try {
    const job = await apiFetch<{ task_id: string }>(
      `/api/v1/licitacoes/ingest/pncp/backfill?${params}`,
      { method: "POST" },
    );
    redirect(`/licitacoes?task=${encodeURIComponent(job.task_id)}`);
  } catch (err) {
    if (err && typeof err === "object" && "digest" in err) throw err;
    redirect(`/licitacoes?erro=${encodeURIComponent("Não foi possível enfileirar a recarga do PNCP.")}`);
  }
}

async function iniciarCargaResultados(formData: FormData): Promise<void> {
  "use server";
  const dias = Number(formData.get("dias") ?? 180);
  const max = Number(formData.get("max_licitacoes") ?? 200);
  const uf = String(formData.get("uf_resultados") ?? "").trim().toUpperCase();
  const params = new URLSearchParams({ dias: String(dias), max_licitacoes: String(max) });
  if (uf) params.set("uf", uf);
  try {
    const job = await apiFetch<{ task_id: string }>(
      `/api/v1/licitacoes/ingest/resultados/dispatch?${params}`,
      { method: "POST" },
    );
    redirect(`/licitacoes?task=${encodeURIComponent(job.task_id)}`);
  } catch (err) {
    if (err && typeof err === "object" && "digest" in err) throw err;
    redirect(`/licitacoes?erro=${encodeURIComponent("Não foi possível enfileirar os resultados comerciais.")}`);
  }
}

function formatCurrency(value: string | null): string {
  if (!value) return "—";
  const num = Number(value);
  if (Number.isNaN(num)) return value;
  return num.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("pt-BR");
}

export default async function LicitacoesPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const searchParams = await props.searchParams;
  const taskId = typeof searchParams.task === "string" ? searchParams.task : "";
  const erroAcao = typeof searchParams.erro === "string" ? searchParams.erro : "";
  const params = new URLSearchParams();
  const uf = typeof searchParams.uf === "string" ? searchParams.uf : "";
  const modalidade = typeof searchParams.modalidade === "string" ? searchParams.modalidade : "";
  const search = typeof searchParams.search === "string" ? searchParams.search : "";
  const page = typeof searchParams.page === "string" ? searchParams.page : "1";
  if (uf) params.set("uf", uf);
  if (modalidade) params.set("modalidade", modalidade);
  if (search) params.set("search", search);
  params.set("page", page);
  params.set("page_size", "20");

  const resp = await fetchLicitacoes(params);
  let taskStatus: { status: string; result?: Record<string, unknown>; progress?: Record<string, unknown>; error?: string } | null = null;
  if (taskId) {
    try {
      taskStatus = await apiFetch<{ status: string; result?: Record<string, unknown>; progress?: Record<string, unknown>; error?: string }>(
        `/api/v1/licitacoes/ingest/tasks/${encodeURIComponent(taskId)}`,
      );
    } catch {
      taskStatus = null;
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Captação"
        title="Licitações"
        subtitle={
          <>
            Fonte: <strong>PNCP</strong> — Portal Nacional de Contratações
            Públicas.
          </>
        }
      />

      {erroAcao && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
          {erroAcao}
        </div>
      )}

      {taskId && (
        <section className="rounded-xl border border-sky-200 bg-sky-50 p-4 text-sm text-sky-950">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-semibold">Atualização do PNCP · {taskStatus?.status ?? "consulta indisponível"}</p>
              <p className="mt-1 font-mono text-xs">Tarefa {taskId}</p>
              {taskStatus?.result && (
                <p className="mt-1">
                  {String(taskStatus.result.total_fetched ?? 0)} publicações lidas; {String(taskStatus.result.inserted ?? 0)} novas.
                  {Array.isArray(taskStatus.result.failed_windows) && taskStatus.result.failed_windows.length > 0
                    ? ` ${taskStatus.result.failed_windows.length} janela(s) precisam ser repetidas.`
                    : ""}
                </p>
              )}
              {taskStatus?.progress && (
                <p className="mt-1">
                  Janela {String(taskStatus.progress.windows_done ?? 0)} de {String(taskStatus.progress.total_windows ?? 0)} · {String(taskStatus.progress.total_fetched ?? 0)} publicações encontradas.
                </p>
              )}
              {taskStatus?.error && <p className="mt-1 text-rose-800">{taskStatus.error}</p>}
            </div>
            <Link href={`/licitacoes?task=${encodeURIComponent(taskId)}`} className="rounded border border-sky-300 px-3 py-1.5 text-xs font-medium hover:bg-sky-100">
              Atualizar estado
            </Link>
          </div>
        </section>
      )}

      <section className="grid gap-4 lg:grid-cols-2">
        <form action={iniciarRecargaPncp} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
          <div>
            <h2 className="text-sm font-semibold">Recuperar captação do PNCP</h2>
            <p className="mt-1 text-xs text-slate-500">Executa no worker em janelas semanais. Reprocessar é idempotente.</p>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col text-xs text-slate-600">De
              <input type="date" name="data_inicial" defaultValue="2026-04-01" required className="mt-1 rounded border border-slate-300 px-2 py-1.5" />
            </label>
            <label className="flex flex-col text-xs text-slate-600">Até
              <input type="date" name="data_final" defaultValue={new Date().toISOString().slice(0, 10)} required className="mt-1 rounded border border-slate-300 px-2 py-1.5" />
            </label>
            <label className="flex flex-col text-xs text-slate-600">UF (opcional)
              <input name="uf" maxLength={2} placeholder="Todas" className="mt-1 w-20 rounded border border-slate-300 px-2 py-1.5 uppercase" />
            </label>
            <button type="submit" className="rounded bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700">Enfileirar recarga</button>
          </div>
        </form>
        <form action={iniciarCargaResultados} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
          <div>
            <h2 className="text-sm font-semibold">Popular inteligência comercial</h2>
            <p className="mt-1 text-xs text-slate-500">Alimenta concorrentes e geotargeting nos painéis. Consulta somente licitações já captadas — por padrão, até 200 publicadas nos últimos 180 dias. Eficiência depende de registrar decisões na triagem.</p>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col text-xs text-slate-600">Publicações dos últimos dias
              <input type="number" name="dias" min={1} max={365} defaultValue={180} required className="mt-1 w-24 rounded border border-slate-300 px-2 py-1.5" />
            </label>
            <label className="flex flex-col text-xs text-slate-600">Limite de licitações
              <input type="number" name="max_licitacoes" min={1} max={5000} defaultValue={200} required className="mt-1 w-24 rounded border border-slate-300 px-2 py-1.5" />
            </label>
            <label className="flex flex-col text-xs text-slate-600">UF (opcional)
              <input name="uf_resultados" maxLength={2} placeholder="Todas" className="mt-1 w-20 rounded border border-slate-300 px-2 py-1.5 uppercase" />
            </label>
            <button type="submit" className="rounded bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700">Enfileirar análise</button>
          </div>
        </form>
      </section>

      <form className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4">
        <label className="flex flex-col text-xs font-medium text-slate-600">
          UF
          <input
            name="uf"
            defaultValue={uf}
            maxLength={2}
            placeholder="ex: SP"
            className="mt-1 w-24 rounded-md border border-slate-300 px-2 py-1 text-sm uppercase"
          />
        </label>
        <label className="flex flex-col text-xs font-medium text-slate-600">
          Modalidade (contém)
          <input
            name="modalidade"
            defaultValue={modalidade}
            placeholder="ex: Pregão"
            className="mt-1 w-56 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-1 flex-col text-xs font-medium text-slate-600">
          Busca no objeto
          <input
            name="search"
            defaultValue={search}
            placeholder="ex: pavimentação asfáltica"
            maxLength={200}
            className="mt-1 w-full min-w-64 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
        >
          Filtrar
        </button>
      </form>

      {resp === null ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          Não foi possível conectar à API (<code>NEXT_PUBLIC_API_BASE_URL</code>). Suba o
          backend com <code>cd apps/api &amp;&amp; .venv/bin/python -m uvicorn app.main:app
          --port 8000</code> (ou <code>docker compose -f infra/docker-compose.yml up</code>,
          se o Docker estiver rodando) e atualize.
        </div>
      ) : resp.data.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
          Nenhuma licitação ingerida ainda. Dispare um crawler:
          <pre className="mt-2 overflow-x-auto rounded bg-slate-900 p-3 text-xs text-slate-100">
            curl -X POST &quot;$API/api/v1/licitacoes/ingest/pncp?uf=SP&amp;max_paginas=2&quot;
          </pre>
        </div>
      ) : (
        <>
          <p className="text-xs text-slate-500">
            {resp.total.toLocaleString("pt-BR")} licitações · página {resp.page}
          </p>
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Publicação</th>
                  <th className="px-4 py-3">UF / Município</th>
                  <th className="px-4 py-3">Órgão</th>
                  <th className="px-4 py-3">Modalidade</th>
                  <th className="px-4 py-3">Objeto</th>
                  <th className="px-4 py-3 text-right">Valor estimado</th>
                  <th className="px-4 py-3">Edital</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {resp.data.map((lic) => (
                  <tr key={lic.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 text-slate-700">
                      {formatDate(lic.data_publicacao_pncp)}
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      {lic.uf_sigla ?? "—"}
                      {lic.municipio_nome ? ` · ${lic.municipio_nome}` : ""}
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      {lic.orgao_razao_social ?? lic.orgao_cnpj ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-700">{lic.modalidade_nome ?? "—"}</td>
                    <td className="max-w-md truncate px-4 py-3 text-slate-700" title={lic.objeto_compra ?? ""}>
                      {lic.objeto_compra ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-slate-700">
                      {formatCurrency(lic.valor_total_estimado)}
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      <div className="flex items-center gap-2">
                        <form action={downloadEdital}>
                          <input type="hidden" name="licitacao_id" value={lic.id} />
                          <button
                            type="submit"
                            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                            title="Baixa o edital + anexos do PNCP (idempotente)"
                          >
                            Baixar
                          </button>
                        </form>
                        <Link
                          href={`/licitacoes/${lic.id}`}
                          className="text-xs text-slate-500 hover:underline"
                        >
                          detalhes
                        </Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={resp.page}
            pageSize={resp.page_size}
            total={resp.total}
            uf={uf}
            modalidade={modalidade}
            search={search}
          />
        </>
      )}
    </div>
  );
}

function Pagination({
  page,
  pageSize,
  total,
  uf,
  modalidade,
  search,
}: {
  page: number;
  pageSize: number;
  total: number;
  uf: string;
  modalidade: string;
  search: string;
}) {
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  if (lastPage <= 1) return null;

  const mkHref = (p: number) => {
    const params = new URLSearchParams();
    if (uf) params.set("uf", uf);
    if (modalidade) params.set("modalidade", modalidade);
    if (search) params.set("search", search);
    params.set("page", String(p));
    return `?${params.toString()}`;
  };

  return (
    <nav className="flex items-center justify-between text-sm">
      {page > 1 ? (
        <Link href={mkHref(page - 1)} className="text-slate-700 hover:underline">
          ← Anterior
        </Link>
      ) : <span />}
      <span className="text-xs text-slate-500">
        Página {page} de {lastPage}
      </span>
      {page < lastPage ? (
        <Link href={mkHref(page + 1)} className="text-slate-700 hover:underline">
          Próxima →
        </Link>
      ) : <span />}
    </nav>
  );
}
