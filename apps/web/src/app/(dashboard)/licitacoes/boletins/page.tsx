import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type SavedQuery = {
  id: number;
  nome: string;
  user_email: string;
  recipients: string[];
  uf: string | null;
  modalidade: string | null;
  search: string | null;
  orgao_cnpj: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export const dynamic = "force-dynamic";

async function fetchSavedQueries(): Promise<SavedQuery[] | null> {
  try {
    return await apiFetch<SavedQuery[]>(
      "/api/v1/licitacoes/boletins/saved-queries",
    );
  } catch {
    return null;
  }
}

async function createSavedQuery(formData: FormData): Promise<void> {
  "use server";
  const payload = {
    nome: String(formData.get("nome") ?? "").trim(),
    user_email: String(formData.get("user_email") ?? "").trim(),
    recipients: String(formData.get("recipients") ?? "")
      .split(",")
      .map((e) => e.trim())
      .filter(Boolean),
    uf: String(formData.get("uf") ?? "").trim() || null,
    modalidade: String(formData.get("modalidade") ?? "").trim() || null,
    search: String(formData.get("search") ?? "").trim() || null,
    orgao_cnpj: String(formData.get("orgao_cnpj") ?? "").trim() || null,
    active: true,
  };
  if (!payload.nome || !payload.user_email || payload.recipients.length === 0) {
    return;
  }
  await apiFetch("/api/v1/licitacoes/boletins/saved-queries", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  revalidatePath("/licitacoes/boletins");
}

async function deleteSavedQuery(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/licitacoes/boletins/saved-queries/${id}`, {
    method: "DELETE",
  });
  revalidatePath("/licitacoes/boletins");
}

async function dispatchNow(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  const qs = id ? `?saved_query_id=${id}` : "";
  await apiFetch(`/api/v1/licitacoes/boletins/dispatch${qs}`, {
    method: "POST",
  });
  revalidatePath("/licitacoes/boletins");
}

function formatFilters(q: SavedQuery): string {
  const parts: string[] = [];
  if (q.uf) parts.push(`UF=${q.uf}`);
  if (q.modalidade) parts.push(`modalidade=${q.modalidade}`);
  if (q.search) parts.push(`busca="${q.search}"`);
  if (q.orgao_cnpj) parts.push(`CNPJ=${q.orgao_cnpj}`);
  return parts.length > 0 ? parts.join(" · ") : "sem filtros (recebe tudo)";
}

export default async function BoletinsPage() {
  const queries = await fetchSavedQueries();

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Boletins por email</h1>
          <p className="mt-1 text-sm text-slate-500">
            Cadastre filtros salvos e receba um digest das novas licitações 3x/dia
            (07h, 13h, 19h — America/Sao_Paulo). Envio via <strong>Resend</strong>.
          </p>
        </div>
        <Link
          href="/licitacoes"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          ← Voltar
        </Link>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="mb-3 text-sm font-semibold text-slate-800">Nova query salva</h2>
        <form action={createSavedQuery} className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Nome <span className="text-rose-500">*</span>
            <input
              name="nome"
              required
              maxLength={120}
              placeholder="ex: Pregões pavimentação MG"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Email do dono <span className="text-rose-500">*</span>
            <input
              name="user_email"
              type="email"
              required
              placeholder="voce@empresa.com"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600 md:col-span-2">
            Destinatários (separados por vírgula) <span className="text-rose-500">*</span>
            <input
              name="recipients"
              required
              placeholder="licitacoes@primor.com, comercial@primor.com"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            UF
            <input
              name="uf"
              maxLength={2}
              placeholder="MG"
              className="mt-1 w-24 rounded-md border border-slate-300 px-2 py-1 text-sm uppercase"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Modalidade (contém)
            <input
              name="modalidade"
              placeholder="Pregão"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600 md:col-span-2">
            Termo de busca no objeto
            <input
              name="search"
              maxLength={200}
              placeholder="ex: pavimentação asfáltica"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600 md:col-span-2">
            CNPJ do órgão (opcional)
            <input
              name="orgao_cnpj"
              maxLength={32}
              placeholder="12.345.678/0001-00"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <div className="md:col-span-2">
            <button
              type="submit"
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
            >
              Salvar query
            </button>
          </div>
        </form>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-800">Queries cadastradas</h2>
        {queries === null ? (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            API indisponível. Suba o backend (<code>uvicorn</code>).
          </div>
        ) : queries.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
            Nenhuma query cadastrada. Crie a primeira no formulário acima.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-600">
                <tr>
                  <th className="px-4 py-2 text-left">Nome</th>
                  <th className="px-4 py-2 text-left">Filtros</th>
                  <th className="px-4 py-2 text-left">Destinatários</th>
                  <th className="px-4 py-2 text-left">Status</th>
                  <th className="px-4 py-2 text-right">Ações</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {queries.map((q) => (
                  <tr key={q.id}>
                    <td className="px-4 py-2 font-medium text-slate-900">
                      {q.nome}
                      <div className="text-xs text-slate-400">id {q.id}</div>
                    </td>
                    <td className="px-4 py-2 text-slate-700">{formatFilters(q)}</td>
                    <td className="px-4 py-2 text-xs text-slate-600">
                      {q.recipients.join(", ")}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={
                          q.active
                            ? "rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800"
                            : "rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600"
                        }
                      >
                        {q.active ? "ativa" : "pausada"}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right">
                      <form action={dispatchNow} className="inline">
                        <input type="hidden" name="id" value={q.id} />
                        <button
                          type="submit"
                          className="mr-2 rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                        >
                          Enviar agora
                        </button>
                      </form>
                      <form action={deleteSavedQuery} className="inline">
                        <input type="hidden" name="id" value={q.id} />
                        <button
                          type="submit"
                          className="rounded border border-rose-300 bg-white px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50"
                        >
                          Excluir
                        </button>
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
