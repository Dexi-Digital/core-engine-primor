import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type DocumentoFiscal = {
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
  obra_id: number | null;
  status_envio: string;
  protocolo_dominio: string | null;
  sent_at: string | null;
  error_msg: string | null;
  retry_count: number;
  source: string | null;
  observacoes: string | null;
  created_at: string;
};

type Obra = { id: number; codigo: string; nome: string };

const TIPOS: Array<[string, string]> = [
  ["nfe", "NF-e"],
  ["nfce", "NFC-e"],
  ["nfse", "NFS-e"],
  ["cte", "CT-e"],
  ["cfe", "CF-e"],
  ["baixa", "Baixa"],
];

const STATUSES: Array<[string, string]> = [
  ["pendente", "Pendente"],
  ["enviando", "Enviando"],
  ["enviado", "Enviado"],
  ["erro", "Erro"],
];

const STATUS_BADGE: Record<string, string> = {
  pendente: "bg-slate-100 text-slate-700",
  enviando: "bg-amber-100 text-amber-700",
  enviado: "bg-emerald-100 text-emerald-700",
  erro: "bg-rose-100 text-rose-700",
};

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const dynamic = "force-dynamic";

function formatCnpj(c: string | null): string {
  if (!c) return "—";
  const n = c.replace(/\D/g, "");
  if (n.length === 14) {
    return `${n.slice(0, 2)}.${n.slice(2, 5)}.${n.slice(5, 8)}/${n.slice(8, 12)}-${n.slice(12)}`;
  }
  if (n.length === 11) {
    return `${n.slice(0, 3)}.${n.slice(3, 6)}.${n.slice(6, 9)}-${n.slice(9)}`;
  }
  return c;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("pt-BR");
  } catch {
    return iso;
  }
}

function formatBRL(v: string | null): string {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return v;
  return n.toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
}

async function fetchDocumentos(params: {
  tipo?: string;
  status_envio?: string;
  search?: string;
  emitida_de?: string;
  emitida_ate?: string;
  obra_id?: string;
  valor_min?: string;
  valor_max?: string;
}): Promise<DocumentoFiscal[]> {
  const qs = new URLSearchParams();
  if (params.tipo) qs.set("tipo", params.tipo);
  if (params.status_envio) qs.set("status_envio", params.status_envio);
  if (params.search) qs.set("search", params.search);
  if (params.emitida_de) qs.set("emitida_de", params.emitida_de);
  if (params.emitida_ate) qs.set("emitida_ate", params.emitida_ate);
  if (params.obra_id) qs.set("obra_id", params.obra_id);
  if (params.valor_min) qs.set("valor_min", params.valor_min);
  if (params.valor_max) qs.set("valor_max", params.valor_max);
  const path = `/api/v1/fiscal/documentos${qs.toString() ? `?${qs}` : ""}`;
  try {
    return await apiFetch<DocumentoFiscal[]>(path);
  } catch {
    return [];
  }
}

async function fetchObras(): Promise<Obra[]> {
  try {
    return await apiFetch<Obra[]>(`/api/v1/obras`);
  } catch {
    return [];
  }
}

async function uploadXml(formData: FormData): Promise<void> {
  "use server";
  const arquivo = formData.get("arquivo") as File | null;
  if (!arquivo || arquivo.size === 0) return;
  const upload = new FormData();
  upload.append("arquivo", arquivo);
  // Server Actions: bater direto na API sem passar pelo `apiFetch`
  // (que forca content-type JSON, incompativel com multipart).
  await fetch(`${API_BASE}/api/v1/fiscal/documentos`, {
    method: "POST",
    body: upload,
  });
  revalidatePath("/fiscal/documentos");
}

async function enviarDominio(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "").trim();
  if (!id) return;
  await fetch(
    `${API_BASE}/api/v1/fiscal/documentos/${id}/enviar-dominio`,
    { method: "POST" },
  );
  revalidatePath("/fiscal/documentos");
}

type SearchParams = Promise<{
  tipo?: string;
  status_envio?: string;
  search?: string;
  emitida_de?: string;
  emitida_ate?: string;
  obra_id?: string;
  valor_min?: string;
  valor_max?: string;
}>;

export default async function FiscalDocumentosPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const sp = await searchParams;
  const filtros = {
    tipo: sp.tipo ?? "",
    status_envio: sp.status_envio ?? "",
    search: sp.search ?? "",
    emitida_de: sp.emitida_de ?? "",
    emitida_ate: sp.emitida_ate ?? "",
    obra_id: sp.obra_id ?? "",
    valor_min: sp.valor_min ?? "",
    valor_max: sp.valor_max ?? "",
  };
  const [documentos, obras] = await Promise.all([
    fetchDocumentos(filtros),
    fetchObras(),
  ]);

  return (
    <div className="space-y-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Documentos fiscais
          </h1>
          <p className="text-sm text-slate-600">
            Importe XMLs (NF-e, NFC-e, NFS-e, CT-e, CF-e, Baixa) e envie para o
            escritório contábil via API Domínio.
          </p>
        </div>
        <Link
          href="/financeiro"
          className="text-sm text-slate-600 underline-offset-4 hover:underline"
        >
          ← voltar para Financeiro
        </Link>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-base font-semibold">Importar XML</h2>
        <p className="mt-1 text-xs text-slate-500">
          O tipo é detectado automaticamente pelo conteúdo do arquivo.
        </p>
        <form
          action={uploadXml}
          encType="multipart/form-data"
          className="mt-4 flex flex-wrap items-end gap-3"
        >
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Arquivo XML
            <input
              name="arquivo"
              type="file"
              accept=".xml,application/xml,text/xml"
              required
              className="block text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-900 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-white hover:file:bg-slate-700"
            />
          </label>
          <button
            type="submit"
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            Importar
          </button>
        </form>
      </section>

      <section>
        <form
          method="get"
          className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
        >
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Tipo
            <select
              name="tipo"
              defaultValue={filtros.tipo}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
            >
              <option value="">Todos</option>
              {TIPOS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Status
            <select
              name="status_envio"
              defaultValue={filtros.status_envio}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
            >
              <option value="">Todos</option>
              {STATUSES.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Busca
            <input
              name="search"
              defaultValue={filtros.search}
              placeholder="chave, número, emitente..."
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            />
          </label>
          <input
            type="date"
            name="emitida_de"
            defaultValue={filtros.emitida_de ?? ""}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
          />
          <input
            type="date"
            name="emitida_ate"
            defaultValue={filtros.emitida_ate ?? ""}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
          />
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Valor mín.
            <input
              type="number"
              step="0.01"
              min="0"
              name="valor_min"
              defaultValue={filtros.valor_min}
              placeholder="0,00"
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Valor máx.
            <input
              type="number"
              step="0.01"
              min="0"
              name="valor_max"
              defaultValue={filtros.valor_max}
              placeholder="0,00"
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Obra
            <select
              name="obra_id"
              defaultValue={filtros.obra_id}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
            >
              <option value="">Todas</option>
              {obras.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.codigo} · {o.nome}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            className="rounded-md bg-slate-200 px-4 py-2 text-sm font-medium hover:bg-slate-300"
          >
            Filtrar
          </button>
        </form>

        <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 text-left">Tipo</th>
                <th className="px-4 py-3 text-left">Número</th>
                <th className="px-4 py-3 text-left">UF</th>
                <th className="px-4 py-3 text-left">Emitente</th>
                <th className="px-4 py-3 text-left">Destinatário</th>
                <th className="px-4 py-3 text-right">Valor</th>
                <th className="px-4 py-3 text-left">Emissão</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-left">Ação</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {documentos.length === 0 ? (
                <tr>
                  <td
                    colSpan={9}
                    className="px-4 py-12 text-center text-sm text-slate-500"
                  >
                    Nenhum documento. Importe um XML acima.
                  </td>
                </tr>
              ) : (
                documentos.map((doc) => (
                  <tr key={doc.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 font-mono text-xs uppercase text-slate-700">
                      {doc.tipo}
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      <Link
                        href={`/fiscal/documentos/${doc.id}`}
                        className="font-medium text-slate-900 hover:underline"
                      >
                        {doc.numero ?? doc.chave_acesso ?? `#${doc.id}`}
                      </Link>
                      {doc.chave_dv_valida === false && (
                        <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                          DV inválido
                        </span>
                      )}
                      {doc.serie ? (
                        <span className="text-xs text-slate-400"> / {doc.serie}</span>
                      ) : null}
                      {doc.chave_acesso ? (
                        <div className="font-mono text-[10px] text-slate-400">
                          {doc.chave_acesso}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      {doc.uf ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      <div>{doc.emitente_nome ?? "—"}</div>
                      <div className="text-xs text-slate-400">
                        {formatCnpj(doc.emitente_cnpj)}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      <div>{doc.destinatario_nome ?? "—"}</div>
                      <div className="text-xs text-slate-400">
                        {formatCnpj(doc.destinatario_cnpj)}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-slate-700">
                      {formatBRL(doc.valor_total)}
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      {formatDate(doc.data_emissao)}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                          STATUS_BADGE[doc.status_envio] ??
                          "bg-slate-100 text-slate-700"
                        }`}
                      >
                        {doc.status_envio}
                      </span>
                      {doc.protocolo_dominio ? (
                        <div className="mt-1 font-mono text-[10px] text-slate-400">
                          {doc.protocolo_dominio}
                        </div>
                      ) : null}
                      {doc.error_msg ? (
                        <div className="mt-1 text-[10px] text-rose-600">
                          {doc.error_msg}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-3">
                      {doc.status_envio !== "enviado" ? (
                        <form action={enviarDominio}>
                          <input type="hidden" name="id" value={doc.id} />
                          <button
                            type="submit"
                            className="rounded-md bg-slate-900 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-700"
                          >
                            Enviar
                          </button>
                        </form>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
