import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

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

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Licitações</h1>
          <p className="mt-1 text-sm text-slate-500">
            Fonte: <strong>PNCP</strong> — Portal Nacional de Contratações Públicas.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/licitacoes/captacao"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Tela de Captação →
          </Link>
          <Link
            href="/licitacoes/certidoes"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Certidões / atestados →
          </Link>
          <Link
            href="/licitacoes/boletins"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Boletins por email →
          </Link>
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-800">
            Implementado
          </span>
        </div>
      </header>

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
          Não foi possível conectar à API (<code>NEXT_PUBLIC_API_BASE_URL</code>). Suba o backend
          com <code>docker compose -f infra/docker-compose.yml up</code> e atualize.
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
