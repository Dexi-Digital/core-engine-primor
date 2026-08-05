import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type LicitacaoRead = {
  id: number;
  external_id: string;
  objeto_compra: string | null;
  modalidade_nome: string | null;
  orgao_razao_social: string | null;
  uf_sigla: string | null;
  municipio_nome: string | null;
  status_triagem: string;
};

type DecisaoRead = {
  id: number;
  decisao: string;
  observacao: string | null;
  usuario_email: string;
  created_at: string;
};

export const dynamic = "force-dynamic";

async function observacaoAction(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("licitacao_id");
  const observacao = String(formData.get("observacao") ?? "").trim();
  if (!id || !observacao) return;
  try {
    await apiFetch(`/api/v1/licitacoes/${id}/triagem/observacao`, {
      method: "POST",
      body: JSON.stringify({ observacao }),
    });
  } catch (err) {
    console.error("[captacao] observacao falhou", err);
  }
  revalidatePath(`/licitacoes/captacao/${id}`);
}

const DECISAO_LABELS: Record<string, string> = {
  aprovado: "Aprovado",
  rejeitado: "Rejeitado",
  observacao: "Observação",
};

export default async function CaptacaoDetalhePage(props: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await props.params;

  let lic: LicitacaoRead | null = null;
  let decisoes: DecisaoRead[] = [];
  try {
    lic = await apiFetch<LicitacaoRead>(`/api/v1/licitacoes/${id}`);
    decisoes = await apiFetch<DecisaoRead[]>(`/api/v1/licitacoes/${id}/triagem`);
  } catch {
    lic = null;
  }

  if (!lic) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        Licitação não encontrada (ou API fora do ar).{" "}
        <Link href="/licitacoes/captacao" className="underline">
          Voltar
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <Link
          href="/licitacoes/captacao"
          className="text-sm text-slate-500 hover:underline"
        >
          ← Tela de Captação
        </Link>
        <h1 className="mt-2 text-2xl font-bold">
          Triagem — {lic.external_id}
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          {lic.objeto_compra ?? "Sem objeto"} · {lic.orgao_razao_social ?? "—"} ·{" "}
          {lic.uf_sigla ?? "—"}
          {lic.municipio_nome ? ` / ${lic.municipio_nome}` : ""} · status:{" "}
          <strong>{lic.status_triagem}</strong>
        </p>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-700">
          Registrar observação
        </h2>
        <form action={observacaoAction} className="mt-2 flex items-start gap-2">
          <input type="hidden" name="licitacao_id" value={lic.id} />
          <textarea
            name="observacao"
            required
            rows={2}
            placeholder="Análise preliminar, restrições, pendências…"
            className="w-full max-w-xl rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
          <button
            type="submit"
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
          >
            Salvar
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700">
          Histórico de decisões
        </h2>
        {decisoes.length === 0 ? (
          <p className="px-4 py-6 text-sm text-slate-500">
            Nenhuma decisão registrada ainda.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {decisoes.map((d) => (
              <li key={d.id} className="px-4 py-3 text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-slate-800">
                    {DECISAO_LABELS[d.decisao] ?? d.decisao}
                  </span>
                  <span className="text-xs text-slate-500">
                    {d.usuario_email} ·{" "}
                    {new Date(d.created_at).toLocaleString("pt-BR")}
                  </span>
                </div>
                {d.observacao ? (
                  <p className="mt-1 text-slate-600">{d.observacao}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
