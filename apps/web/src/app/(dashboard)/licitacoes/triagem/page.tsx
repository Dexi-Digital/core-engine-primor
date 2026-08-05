import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type TriagemRow = {
  licitacao_id: number;
  status_triagem: string;
  uf_sigla: string | null;
  municipio_nome: string | null;
  orgao_razao_social: string | null;
  objeto_compra: string | null;
  modalidade_nome: string | null;
  valor_total_estimado: string | null;
  data_publicacao_pncp: string | null;
  link_portal: string | null;
  link_pasta: string | null;
  link_planilha: string | null;
  planilha_nome: string | null;
  anexos_count: number;
  observacao: string | null;
};

type TriagemResponse = {
  total: number;
  page: number;
  page_size: number;
  data: TriagemRow[];
};

export const dynamic = "force-dynamic";

// Critério de UAT do cliente: alerta VERMELHO quando a planilha
// orçamentária não foi localizada (status sem_planilha) e nos erros.
// Cores alinhadas com o STATUS_BADGE de /licitacoes/captacao (mesma
// informação, mesmas cores entre páginas irmãs).
const STATUS_BADGE: Record<string, { label: string; className: string }> = {
  novo_captado: { label: "Novo Captado", className: "bg-sky-100 text-sky-800" },
  em_analise: { label: "Em Análise", className: "bg-amber-100 text-amber-800" },
  aprovado: { label: "Aprovado", className: "bg-emerald-100 text-emerald-800" },
  rejeitado: { label: "Rejeitado", className: "bg-slate-200 text-slate-600" },
  processando_anexos: {
    label: "Processando Anexos",
    className: "bg-indigo-100 text-indigo-800",
  },
  completo: { label: "Completo", className: "bg-emerald-100 text-emerald-800" },
  sem_planilha: { label: "Sem Planilha", className: "bg-red-100 text-red-800" },
  erro_portal: { label: "Erro de Portal", className: "bg-red-100 text-red-800" },
  erro_sharepoint: {
    label: "Erro SharePoint",
    className: "bg-red-100 text-red-800",
  },
};

async function fetchTriagem(params: URLSearchParams): Promise<TriagemResponse | null> {
  try {
    return await apiFetch<TriagemResponse>(
      `/api/v1/licitacoes/triagem?${params.toString()}`,
    );
  } catch {
    return null;
  }
}

async function reprocessar(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/processar-anexos`, {
      method: "POST",
    });
  } catch (err) {
    console.error("[triagem] reprocessamento falhou", err);
  }
  revalidatePath("/licitacoes/triagem");
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

export default async function TriagemPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const searchParams = await props.searchParams;
  const params = new URLSearchParams();
  const status = typeof searchParams.status === "string" ? searchParams.status : "";
  const uf = typeof searchParams.uf === "string" ? searchParams.uf : "";
  const page = typeof searchParams.page === "string" ? searchParams.page : "1";
  if (status) params.set("status", status);
  if (uf) params.set("uf", uf);
  params.set("page", page);
  params.set("page_size", "50");

  const triagem = await fetchTriagem(params);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link href="/licitacoes" className="text-sm text-slate-500 hover:underline">
            ← Licitações
          </Link>
          <h1 className="text-xl font-semibold">Aba de Triagem</h1>
          <p className="text-sm text-slate-500">
            Editais aprovados com links diretos para pasta do projeto e
            planilha orçamentária.
          </p>
        </div>
        <form className="flex items-end gap-2">
          <label className="text-sm">
            <span className="block text-slate-500">Status</span>
            <select
              name="status"
              defaultValue={status}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="">Todos</option>
              {Object.entries(STATUS_BADGE).map(([value, meta]) => (
                <option key={value} value={value}>
                  {meta.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="block text-slate-500">UF</span>
            <input
              name="uf"
              defaultValue={uf}
              maxLength={2}
              className="w-14 rounded border border-slate-300 px-2 py-1 text-sm uppercase"
            />
          </label>
          <button
            type="submit"
            className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
          >
            Filtrar
          </button>
        </form>
      </div>

      {!triagem ? (
        <p className="text-sm text-red-600">
          Não foi possível carregar a triagem — API indisponível.
        </p>
      ) : triagem.data.length === 0 ? (
        <p className="text-sm text-slate-500">Nenhuma licitação encontrada.</p>
      ) : (
        <div className="overflow-x-auto rounded border border-slate-200">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-600">
              <tr>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Município / UF</th>
                <th className="px-3 py-2">Órgão</th>
                <th className="px-3 py-2">Objeto</th>
                <th className="px-3 py-2">Valor estimado</th>
                <th className="px-3 py-2">Publicação</th>
                <th className="px-3 py-2">Links</th>
                <th className="px-3 py-2">Observação</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {triagem.data.map((row) => {
                const badge =
                  STATUS_BADGE[row.status_triagem] ?? {
                    label: row.status_triagem,
                    className: "bg-slate-100 text-slate-700",
                  };
                const canReprocess =
                  row.status_triagem === "aprovado" ||
                  row.status_triagem.startsWith("erro") ||
                  row.status_triagem === "sem_planilha";
                return (
                  <tr key={row.licitacao_id} className="border-t border-slate-100">
                    <td className="px-3 py-2">
                      <span
                        className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${badge.className}`}
                      >
                        {badge.label}
                      </span>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {row.municipio_nome ?? "—"} / {row.uf_sigla ?? "—"}
                    </td>
                    <td className="max-w-48 truncate px-3 py-2">
                      {row.orgao_razao_social ?? "—"}
                    </td>
                    <td className="max-w-64 truncate px-3 py-2">
                      {row.objeto_compra ?? "—"}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {formatCurrency(row.valor_total_estimado)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {formatDate(row.data_publicacao_pncp)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex gap-2">
                        {row.link_portal ? (
                          <a
                            href={row.link_portal}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-700 hover:underline"
                          >
                            Edital
                          </a>
                        ) : null}
                        <Link
                          href={
                            row.link_pasta ?? `/licitacoes/${row.licitacao_id}`
                          }
                          target={row.link_pasta ? "_blank" : undefined}
                          rel={row.link_pasta ? "noopener noreferrer" : undefined}
                          className="text-blue-700 hover:underline"
                        >
                          Pasta ({row.anexos_count})
                        </Link>
                        {row.link_planilha ? (
                          <a
                            href={row.link_planilha}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-medium text-emerald-700 hover:underline"
                            title={row.planilha_nome ?? undefined}
                          >
                            Planilha
                          </a>
                        ) : (
                          <span className="font-medium text-red-600">
                            Sem planilha
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="max-w-48 truncate px-3 py-2 text-slate-500">
                      {row.observacao ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      {canReprocess ? (
                        <form action={reprocessar}>
                          <input
                            type="hidden"
                            name="licitacao_id"
                            value={row.licitacao_id}
                          />
                          <button
                            type="submit"
                            className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
                          >
                            Processar anexos
                          </button>
                        </form>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {triagem && triagem.data.length > 0 ? (
        <Pagination
          page={triagem.page}
          pageSize={triagem.page_size}
          total={triagem.total}
          status={status}
          uf={uf}
        />
      ) : null}
    </div>
  );
}

function Pagination({
  page,
  pageSize,
  total,
  status,
  uf,
}: {
  page: number;
  pageSize: number;
  total: number;
  status: string;
  uf: string;
}) {
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  if (lastPage <= 1) return null;

  const mkHref = (p: number) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (uf) params.set("uf", uf);
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
