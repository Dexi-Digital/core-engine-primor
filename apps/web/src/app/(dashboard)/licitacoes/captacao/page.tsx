import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type LicitacaoRead = {
  id: number;
  external_id: string;
  objeto_compra: string | null;
  modalidade_nome: string | null;
  valor_total_estimado: string | null;
  orgao_razao_social: string | null;
  orgao_cnpj: string | null;
  uf_sigla: string | null;
  municipio_nome: string | null;
  data_publicacao_pncp: string | null;
  status_triagem: string;
};

type ListResponse = {
  total: number;
  page: number;
  page_size: number;
  data: LicitacaoRead[];
};

export const dynamic = "force-dynamic";

const STATUS_LABELS: Record<string, string> = {
  novo_captado: "Novo Captado",
  em_analise: "Em Análise",
  aprovado: "Aprovado",
  rejeitado: "Rejeitado",
  processando_anexos: "Processando Anexos",
  completo: "Completo",
  sem_planilha: "Sem Planilha",
  erro_portal: "Erro de Portal",
  erro_sharepoint: "Erro SharePoint",
};

const STATUS_BADGE: Record<string, string> = {
  novo_captado: "bg-sky-100 text-sky-800",
  em_analise: "bg-amber-100 text-amber-800",
  aprovado: "bg-emerald-100 text-emerald-800",
  rejeitado: "bg-slate-200 text-slate-600",
  processando_anexos: "bg-indigo-100 text-indigo-800",
  completo: "bg-emerald-100 text-emerald-800",
  sem_planilha: "bg-red-100 text-red-800",
  erro_portal: "bg-red-100 text-red-800",
  erro_sharepoint: "bg-red-100 text-red-800",
};

async function aprovarAction(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/triagem/aprovar`, {
      method: "POST",
      body: JSON.stringify({ observacao: null }),
    });
  } catch (err) {
    console.error("[captacao] aprovar falhou", err);
  }
  revalidatePath("/licitacoes/captacao");
}

async function rejeitarAction(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  const observacao = String(formData.get("observacao") ?? "").trim();
  if (!id || observacao.length < 5) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/triagem/rejeitar`, {
      method: "POST",
      body: JSON.stringify({ observacao }),
    });
  } catch (err) {
    console.error("[captacao] rejeitar falhou", err);
  }
  revalidatePath("/licitacoes/captacao");
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

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_BADGE[status] ?? "bg-slate-100 text-slate-700"}`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

export default async function CaptacaoPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const searchParams = await props.searchParams;
  const params = new URLSearchParams();
  const uf = typeof searchParams.uf === "string" ? searchParams.uf : "";
  const municipio =
    typeof searchParams.municipio === "string" ? searchParams.municipio : "";
  const status =
    typeof searchParams.status === "string" ? searchParams.status : "";
  const modalidade =
    typeof searchParams.modalidade === "string" ? searchParams.modalidade : "";
  const search = typeof searchParams.search === "string" ? searchParams.search : "";
  const page = typeof searchParams.page === "string" ? searchParams.page : "1";
  if (uf) params.set("uf", uf);
  if (municipio) params.set("municipio", municipio);
  if (status) params.set("status_triagem", status);
  if (modalidade) params.set("modalidade", modalidade);
  if (search) params.set("search", search);
  params.set("page", page);
  params.set("page_size", "20");

  let resp: ListResponse | null = null;
  try {
    resp = await apiFetch<ListResponse>(`/api/v1/licitacoes?${params.toString()}`);
  } catch {
    resp = null;
  }

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Tela de Captação</h1>
          <p className="mt-1 text-sm text-slate-500">
            Triagem de editais captados: aprovar dispara pasta + anexos +
            planilha orçamentária; rejeitar exige motivo.
          </p>
        </div>
        <Link
          href="/licitacoes"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          ← Licitações
        </Link>
      </header>

      <form className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4">
        <label className="flex flex-col text-xs font-medium text-slate-600">
          UF
          <input
            name="uf"
            defaultValue={uf}
            maxLength={2}
            placeholder="ex: MG"
            className="mt-1 w-24 rounded-md border border-slate-300 px-2 py-1 text-sm uppercase"
          />
        </label>
        <label className="flex flex-col text-xs font-medium text-slate-600">
          Município (contém)
          <input
            name="municipio"
            defaultValue={municipio}
            placeholder="ex: Belo Horizonte"
            className="mt-1 w-48 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-col text-xs font-medium text-slate-600">
          Status
          <select
            name="status"
            defaultValue={status}
            className="mt-1 w-48 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">Todos</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs font-medium text-slate-600">
          Modalidade (contém)
          <input
            name="modalidade"
            defaultValue={modalidade}
            placeholder="ex: Pregão"
            className="mt-1 w-40 rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-1 flex-col text-xs font-medium text-slate-600">
          Busca no objeto
          <input
            name="search"
            defaultValue={search}
            placeholder="ex: pavimentação"
            maxLength={200}
            className="mt-1 w-full min-w-56 rounded-md border border-slate-300 px-2 py-1 text-sm"
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
          Não foi possível conectar à API (<code>NEXT_PUBLIC_API_BASE_URL</code>).
        </div>
      ) : resp.data.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
          Nenhuma licitação para os filtros escolhidos.
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
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Publicação</th>
                  <th className="px-4 py-3">UF / Município</th>
                  <th className="px-4 py-3">Órgão</th>
                  <th className="px-4 py-3">Objeto</th>
                  <th className="px-4 py-3 text-right">Valor estimado</th>
                  <th className="px-4 py-3">Triagem</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {resp.data.map((lic) => {
                  const decidivel =
                    lic.status_triagem === "novo_captado" ||
                    lic.status_triagem === "em_analise";
                  return (
                    <tr key={lic.id} className="align-top hover:bg-slate-50">
                      <td className="px-4 py-3">
                        <StatusBadge status={lic.status_triagem} />
                      </td>
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
                      <td
                        className="max-w-md truncate px-4 py-3 text-slate-700"
                        title={lic.objeto_compra ?? ""}
                      >
                        {lic.objeto_compra ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-slate-700">
                        {formatCurrency(lic.valor_total_estimado)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-1">
                          {decidivel ? (
                            <>
                              <form action={aprovarAction}>
                                <input
                                  type="hidden"
                                  name="licitacao_id"
                                  value={lic.id}
                                />
                                <button
                                  type="submit"
                                  className="rounded-md bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-500"
                                >
                                  Aprovar
                                </button>
                              </form>
                              <details>
                                <summary className="cursor-pointer text-xs text-red-700 hover:underline">
                                  Rejeitar…
                                </summary>
                                <form
                                  action={rejeitarAction}
                                  className="mt-1 flex flex-col gap-1"
                                >
                                  <input
                                    type="hidden"
                                    name="licitacao_id"
                                    value={lic.id}
                                  />
                                  <textarea
                                    name="observacao"
                                    required
                                    minLength={5}
                                    rows={2}
                                    placeholder="Motivo (obrigatório)"
                                    className="w-48 rounded-md border border-slate-300 px-2 py-1 text-xs"
                                  />
                                  <button
                                    type="submit"
                                    className="self-start rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-500"
                                  >
                                    Confirmar rejeição
                                  </button>
                                </form>
                              </details>
                            </>
                          ) : null}
                          <Link
                            href={`/licitacoes/captacao/${lic.id}`}
                            className="text-xs text-slate-500 hover:underline"
                          >
                            histórico →
                          </Link>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
