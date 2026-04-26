import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type Obra = {
  id: number;
  codigo: string;
  nome: string;
  cliente: string | null;
  uf: string | null;
  cidade: string | null;
  status: string;
  data_inicio: string | null;
  encerramento_previsto: string | null;
  data_encerramento: string | null;
  observacoes: string | null;
};

const STATUS_BADGE: Record<string, string> = {
  ativa: "bg-emerald-100 text-emerald-700",
  encerrada: "bg-slate-200 text-slate-600",
  suspensa: "bg-amber-100 text-amber-700",
};

export const dynamic = "force-dynamic";

async function fetchObras(): Promise<Obra[]> {
  try {
    return await apiFetch<Obra[]>("/api/v1/obras?limit=200");
  } catch {
    return [];
  }
}

async function createObra(formData: FormData): Promise<void> {
  "use server";
  const codigo = String(formData.get("codigo") ?? "").trim();
  const nome = String(formData.get("nome") ?? "").trim();
  if (!codigo || !nome) return;
  await apiFetch("/api/v1/obras", {
    method: "POST",
    body: JSON.stringify({
      codigo,
      nome,
      cliente: String(formData.get("cliente") ?? "").trim() || null,
      uf: String(formData.get("uf") ?? "").trim() || null,
      cidade: String(formData.get("cidade") ?? "").trim() || null,
      status: String(formData.get("status") ?? "ativa"),
      data_inicio:
        String(formData.get("data_inicio") ?? "").trim() || null,
      encerramento_previsto:
        String(formData.get("encerramento_previsto") ?? "").trim() || null,
    }),
  });
  revalidatePath("/obras");
}

async function deleteObra(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/obras/${id}`, { method: "DELETE" });
  revalidatePath("/obras");
}

function formatDate(d: string | null): string {
  if (!d) return "—";
  const [y, m, day] = d.split("-");
  return `${day}/${m}/${y}`;
}

export default async function ObrasPage() {
  const obras = await fetchObras();

  return (
    <div className="flex flex-col gap-8">
      <header>
        <h1 className="text-2xl font-semibold">Obras</h1>
        <p className="mt-1 text-sm text-slate-600">
          Cadastro de canteiros / obras. Cada obra agrupa documentos
          (ART/RRT, alvará, PCMAT, CIPA, diário) avaliados pelo
          diagnóstico documental.
        </p>
      </header>

      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="mb-4 text-lg font-semibold">Nova obra</h2>
        <form
          action={createObra}
          className="grid grid-cols-1 gap-3 md:grid-cols-3"
        >
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Código *</span>
            <input
              name="codigo"
              required
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs md:col-span-2">
            <span className="mb-1 font-medium text-slate-700">Nome *</span>
            <input
              name="nome"
              required
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs md:col-span-2">
            <span className="mb-1 font-medium text-slate-700">Cliente</span>
            <input
              name="cliente"
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">UF</span>
            <input
              name="uf"
              maxLength={2}
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm uppercase"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Cidade</span>
            <input
              name="cidade"
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Status</span>
            <select
              name="status"
              defaultValue="ativa"
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="ativa">ativa</option>
              <option value="suspensa">suspensa</option>
              <option value="encerrada">encerrada</option>
            </select>
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">
              Data início
            </span>
            <input
              type="date"
              name="data_inicio"
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">
              Encerramento previsto
            </span>
            <input
              type="date"
              name="encerramento_previsto"
              className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
          <div className="md:col-span-3">
            <button
              type="submit"
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
            >
              Cadastrar obra
            </button>
          </div>
        </form>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="mb-3 text-lg font-semibold">
          Obras cadastradas{" "}
          <span className="text-xs font-normal text-slate-500">
            ({obras.length})
          </span>
        </h2>
        {obras.length === 0 ? (
          <p className="text-sm text-slate-500">
            Nenhuma obra cadastrada. Use o formulário acima para começar.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wider text-slate-500">
              <tr>
                <th className="py-2">Código</th>
                <th className="py-2">Nome / Cliente</th>
                <th className="py-2">Local</th>
                <th className="py-2">Status</th>
                <th className="py-2">Início</th>
                <th className="py-2">Prev. encerr.</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {obras.map((o) => (
                <tr key={o.id} className="border-b border-slate-100">
                  <td className="py-2 font-mono text-xs">{o.codigo}</td>
                  <td className="py-2">
                    <div className="font-medium">{o.nome}</div>
                    {o.cliente && (
                      <div className="text-xs text-slate-500">
                        {o.cliente}
                      </div>
                    )}
                  </td>
                  <td className="py-2 text-slate-700">
                    {o.cidade && o.uf
                      ? `${o.cidade}/${o.uf}`
                      : o.uf || o.cidade || "—"}
                  </td>
                  <td className="py-2">
                    <span
                      className={`rounded px-2 py-0.5 text-xs ${
                        STATUS_BADGE[o.status] ?? ""
                      }`}
                    >
                      {o.status}
                    </span>
                  </td>
                  <td className="py-2">{formatDate(o.data_inicio)}</td>
                  <td className="py-2">
                    {formatDate(o.encerramento_previsto)}
                  </td>
                  <td className="py-2 text-right">
                    <form action={deleteObra} className="inline">
                      <input type="hidden" name="id" value={o.id} />
                      <button
                        type="submit"
                        className="text-xs text-red-600 hover:underline"
                      >
                        excluir
                      </button>
                    </form>
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
