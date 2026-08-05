import Link from "next/link";
import { notFound } from "next/navigation";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type ItemNota = {
  id: number;
  ordem: number;
  codigo: string | null;
  descricao: string | null;
  ncm: string | null;
  cfop: string | null;
  unidade: string | null;
  quantidade: string | null;
  valor_unitario: string | null;
  valor_total: string | null;
};

type DocumentoDetail = {
  id: number;
  tipo: string;
  chave_acesso: string | null;
  numero: string | null;
  serie: string | null;
  emitente_cnpj: string | null;
  emitente_nome: string | null;
  destinatario_cnpj: string | null;
  destinatario_nome: string | null;
  valor_total: string | null;
  data_emissao: string | null;
  uf: string | null;
  chave_dv_valida: boolean | null;
  valor_icms: string | null;
  valor_ipi: string | null;
  valor_pis: string | null;
  valor_cofins: string | null;
  obra_id: number | null;
  status_envio: string;
  observacoes: string | null;
  itens: ItemNota[];
};

type Obra = { id: number; codigo: string; nome: string };

export const dynamic = "force-dynamic";

function brl(v: string | null): string {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return v;
  return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

async function fetchDoc(id: number): Promise<DocumentoDetail | null> {
  try {
    return await apiFetch<DocumentoDetail>(`/api/v1/fiscal/documentos/${id}`);
  } catch {
    return null;
  }
}

export default async function DocumentoFiscalDetalhePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: idStr } = await params;
  const id = Number(idStr);
  if (!Number.isFinite(id)) notFound();

  const [doc, obras] = await Promise.all([
    fetchDoc(id),
    apiFetch<Obra[]>(`/api/v1/obras`),
  ]);
  if (!doc) notFound();

  async function vincularObra(formData: FormData) {
    "use server";
    const raw = formData.get("obra_id");
    const obra_id = raw ? Number(raw) : null;
    try {
      await apiFetch(`/api/v1/fiscal/documentos/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ obra_id }),
      });
    } catch (err) {
      console.error("[fiscal-documento-detail] vincular obra failed", err);
    }
    revalidatePath(`/fiscal/documentos/${id}`);
  }

  const impostos: Array<[string, string | null]> = [
    ["ICMS", doc.valor_icms],
    ["IPI", doc.valor_ipi],
    ["PIS", doc.valor_pis],
    ["COFINS", doc.valor_cofins],
  ];

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/fiscal/documentos"
          className="text-sm text-slate-500 hover:underline"
        >
          ← Documentos fiscais
        </Link>
        <h1 className="mt-1 text-2xl font-semibold text-slate-900">
          {doc.tipo.toUpperCase()} {doc.numero ?? ""}{" "}
          {doc.serie ? `/ Série ${doc.serie}` : ""}
        </h1>
        <p className="mt-1 font-mono text-xs text-slate-500">
          {doc.chave_acesso ?? "sem chave de acesso"}
          {doc.chave_dv_valida === false && (
            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
              DV inválido
            </span>
          )}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs text-slate-500">Valor total</p>
          <p className="text-lg font-semibold">{brl(doc.valor_total)}</p>
        </div>
        {impostos.map(([nome, valor]) => (
          <div
            key={nome}
            className="rounded-xl border border-slate-200 bg-white p-4"
          >
            <p className="text-xs text-slate-500">{nome}</p>
            <p className="text-lg font-semibold">{brl(valor)}</p>
          </div>
        ))}
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs text-slate-500">UF</p>
          <p className="text-lg font-semibold">{doc.uf ?? "—"}</p>
        </div>
      </div>

      <form
        action={vincularObra}
        className="flex items-end gap-3 rounded-xl border border-slate-200 bg-white p-4"
      >
        <label className="flex-1 text-sm">
          <span className="mb-1 block text-xs text-slate-500">
            Obra vinculada
          </span>
          <select
            name="obra_id"
            defaultValue={doc.obra_id ?? ""}
            className="w-full rounded-lg border border-slate-200 px-3 py-2"
          >
            <option value="">— sem obra —</option>
            {obras.map((o) => (
              <option key={o.id} value={o.id}>
                {o.codigo} · {o.nome}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          Salvar
        </button>
      </form>

      <div className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-900">
          Itens ({doc.itens.length})
        </h2>
        {doc.itens.length === 0 ? (
          <p className="px-4 py-6 text-sm text-slate-500">
            Sem itens extraídos — use “Reprocessar” na API para notas
            importadas antes da extração de itens, ou o tipo não suporta
            itens (NFS-e, CT-e, CF-e, Baixa).
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs text-slate-500">
                  <th className="px-4 py-2">#</th>
                  <th className="px-4 py-2">Código</th>
                  <th className="px-4 py-2">Descrição</th>
                  <th className="px-4 py-2">NCM</th>
                  <th className="px-4 py-2">CFOP</th>
                  <th className="px-4 py-2 text-right">Qtd</th>
                  <th className="px-4 py-2 text-right">Vlr unit.</th>
                  <th className="px-4 py-2 text-right">Total</th>
                </tr>
              </thead>
              <tbody>
                {doc.itens.map((item) => (
                  <tr key={item.id} className="border-b border-slate-50">
                    <td className="px-4 py-2 text-slate-500">{item.ordem}</td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {item.codigo ?? "—"}
                    </td>
                    <td className="px-4 py-2">{item.descricao ?? "—"}</td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {item.ncm ?? "—"}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {item.cfop ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {item.quantidade ?? "—"} {item.unidade ?? ""}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {brl(item.valor_unitario)}
                    </td>
                    <td className="px-4 py-2 text-right font-medium">
                      {brl(item.valor_total)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
