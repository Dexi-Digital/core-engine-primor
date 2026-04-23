import Link from "next/link";
import { notFound } from "next/navigation";
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

type AnexoRead = {
  id: number;
  sequencial_documento: number;
  titulo: string | null;
  tipo_documento: string | null;
  source_url: string;
  filename: string;
  size_bytes: number | null;
  content_type: string | null;
  downloaded_at: string;
};

type EditalRead = {
  id: number;
  licitacao_id: number;
  source: string;
  status: string;
  anexos_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  anexos: AnexoRead[];
};

export const dynamic = "force-dynamic";

async function fetchLicitacao(id: string): Promise<LicitacaoRead | null> {
  try {
    return await apiFetch<LicitacaoRead>(`/api/v1/licitacoes/${id}`);
  } catch {
    return null;
  }
}

async function fetchEdital(id: string): Promise<EditalRead | null> {
  try {
    return await apiFetch<EditalRead>(`/api/v1/licitacoes/${id}/edital`);
  } catch {
    return null;
  }
}

async function downloadEditalAction(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/edital/download`, { method: "POST" });
  } catch (err) {
    console.error("[licitacao-detail] edital download failed", err);
  }
  revalidatePath(`/licitacoes/${id}`);
}

function formatSize(bytes: number | null): string {
  if (!bytes) return "—";
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  const kb = bytes / 1024;
  return `${kb.toFixed(1)} KB`;
}

function statusBadge(status: string): { label: string; className: string } {
  switch (status) {
    case "completed":
      return {
        label: "Baixado",
        className: "bg-emerald-100 text-emerald-800",
      };
    case "pending":
      return { label: "Pendente", className: "bg-amber-100 text-amber-800" };
    case "empty":
      return { label: "Sem anexos", className: "bg-slate-100 text-slate-700" };
    case "failed":
      return { label: "Falhou", className: "bg-rose-100 text-rose-800" };
    default:
      return { label: status, className: "bg-slate-100 text-slate-700" };
  }
}

export default async function LicitacaoDetailPage(props: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await props.params;
  const [licitacao, edital] = await Promise.all([fetchLicitacao(id), fetchEdital(id)]);
  if (!licitacao) {
    notFound();
  }

  const badge = edital ? statusBadge(edital.status) : null;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/licitacoes" className="text-sm text-slate-500 hover:underline">
          ← Voltar para a lista
        </Link>
      </div>
      <header>
        <h1 className="text-2xl font-bold">
          {licitacao.modalidade_nome ?? "Licitação"} · {licitacao.numero_compra ?? licitacao.external_id}
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          {licitacao.uf_sigla ?? "—"}
          {licitacao.municipio_nome ? ` · ${licitacao.municipio_nome}` : ""}
          {licitacao.orgao_razao_social ? ` · ${licitacao.orgao_razao_social}` : ""}
        </p>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-800">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Objeto
        </h2>
        <p className="whitespace-pre-wrap">{licitacao.objeto_compra ?? "—"}</p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <header className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold">Edital e anexos</h2>
            <p className="mt-1 text-xs text-slate-500">
              Fonte primária: <strong>PNCP</strong>. Fallback ComprasNet / Licitações-e em roadmap.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {badge && (
              <span className={`rounded-full px-3 py-1 text-xs font-semibold ${badge.className}`}>
                {badge.label}
              </span>
            )}
            <form action={downloadEditalAction}>
              <input type="hidden" name="licitacao_id" value={licitacao.id} />
              <button
                type="submit"
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
              >
                {edital ? "Atualizar" : "Baixar edital"}
              </button>
            </form>
          </div>
        </header>

        {edital === null ? (
          <p className="text-sm text-slate-500">
            Nenhum download solicitado ainda. Clique em <strong>Baixar edital</strong> para puxar os
            arquivos do PNCP.
          </p>
        ) : edital.status === "empty" ? (
          <p className="text-sm text-slate-500">
            O PNCP não tem arquivos publicados para esta contratação.
          </p>
        ) : edital.status === "failed" ? (
          <p className="text-sm text-rose-700">
            Falha no download: <code>{edital.error_message ?? "sem mensagem"}</code>. Tente de novo;
            o processo é idempotente e não duplica arquivos.
          </p>
        ) : edital.anexos.length === 0 ? (
          <p className="text-sm text-slate-500">Nenhum anexo registrado ainda.</p>
        ) : (
          <ul className="divide-y divide-slate-100 text-sm">
            {edital.anexos.map((a) => (
              <li key={a.id} className="flex items-center justify-between py-2">
                <div>
                  <p className="font-medium text-slate-800">
                    {a.titulo ?? a.filename}
                    <span className="ml-2 text-xs font-normal text-slate-500">
                      {a.tipo_documento ?? ""}
                    </span>
                  </p>
                  <p className="text-xs text-slate-500">
                    {a.filename} · {formatSize(a.size_bytes)}
                  </p>
                </div>
                <a
                  href={a.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-slate-700 hover:underline"
                >
                  ver no PNCP ↗
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
