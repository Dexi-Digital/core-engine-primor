import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type Certidao = {
  id: number;
  empresa_cnpj: string;
  tipo: string;
  numero: string | null;
  emissao: string | null;
  validade: string | null;
  arquivo_path: string | null;
  orgao_emissor: string | null;
  observacoes: string | null;
  status_atual: string | null;
  dias_para_vencer: number | null;
  created_at: string;
  updated_at: string;
};

// Tipos canonicos espelham o enum do backend (`certidoes.TIPOS_CERTIDAO`).
// Fica duplicado aqui de proposito -- o backend aceita strings livres,
// e a UI nao precisa fazer fetch so para listar a opcao no select.
const TIPOS: Array<[string, string]> = [
  ["CND_FEDERAL", "CND Federal"],
  ["FGTS", "FGTS (CRF)"],
  ["CNDT", "CNDT — Trabalhista"],
  ["INSS", "INSS"],
  ["ESTADUAL", "Estadual"],
  ["MUNICIPAL", "Municipal"],
  ["FALENCIA", "Falência / Rec. Judicial"],
  ["CADIN", "CADIN"],
  ["ATESTADO_CAT", "Atestado CAT"],
  ["ACERVO_TECNICO", "Acervo Técnico"],
  ["OUTRO", "Outro"],
];
const TIPO_LABEL: Record<string, string> = Object.fromEntries(TIPOS);

export const dynamic = "force-dynamic";

async function fetchCertidoes(
  params: { status?: string; tipo?: string; empresa_cnpj?: string } = {},
): Promise<Certidao[] | null> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.tipo) qs.set("tipo", params.tipo);
  if (params.empresa_cnpj) qs.set("empresa_cnpj", params.empresa_cnpj);
  const path = `/api/v1/licitacoes/certidoes${qs.toString() ? `?${qs}` : ""}`;
  try {
    return await apiFetch<Certidao[]>(path);
  } catch {
    return null;
  }
}

async function createCertidao(formData: FormData): Promise<void> {
  "use server";
  const empresa_cnpj = String(formData.get("empresa_cnpj") ?? "").trim();
  const tipo = String(formData.get("tipo") ?? "").trim();
  if (!empresa_cnpj || !tipo) return;
  const payload: Record<string, unknown> = {
    empresa_cnpj,
    tipo,
    numero: String(formData.get("numero") ?? "").trim() || null,
    emissao: String(formData.get("emissao") ?? "").trim() || null,
    validade: String(formData.get("validade") ?? "").trim() || null,
    orgao_emissor: String(formData.get("orgao_emissor") ?? "").trim() || null,
    observacoes: String(formData.get("observacoes") ?? "").trim() || null,
  };
  await apiFetch("/api/v1/licitacoes/certidoes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  revalidatePath("/licitacoes/certidoes");
}

async function deleteCertidao(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/licitacoes/certidoes/${id}`, { method: "DELETE" });
  revalidatePath("/licitacoes/certidoes");
}

function statusBadge(status: string | null): {
  label: string;
  className: string;
} {
  switch (status) {
    case "vigente":
      return { label: "Vigente", className: "bg-emerald-100 text-emerald-800" };
    case "vencendo":
      return { label: "Vencendo", className: "bg-amber-100 text-amber-800" };
    case "vencido":
      return { label: "Vencido", className: "bg-rose-100 text-rose-800" };
    case "sem_validade":
      return { label: "Sem validade", className: "bg-slate-100 text-slate-700" };
    default:
      return { label: status ?? "—", className: "bg-slate-100 text-slate-700" };
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  // YYYY-MM-DD -> DD/MM/YYYY (sem timezone para evitar shift de 1 dia).
  const [y, m, d] = iso.slice(0, 10).split("-");
  if (!y || !m || !d) return iso;
  return `${d}/${m}/${y}`;
}

type SearchParams = { status?: string; tipo?: string; empresa_cnpj?: string };

export default async function CertidoesPage(props: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await props.searchParams;
  const certidoes = await fetchCertidoes(params);

  // Computar contagens por status ANTES de devolver para o status filter mostrar.
  const counts = {
    total: certidoes?.length ?? 0,
    vigente: certidoes?.filter((c) => c.status_atual === "vigente").length ?? 0,
    vencendo:
      certidoes?.filter((c) => c.status_atual === "vencendo").length ?? 0,
    vencido: certidoes?.filter((c) => c.status_atual === "vencido").length ?? 0,
  };

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Certidões e atestados</h1>
          <p className="mt-1 text-sm text-slate-500">
            Gestão de CNDs, FGTS, CNDT, atestados CAT etc. com alerta automático
            de vencimento por email (30, 15, 7 e 0 dias antes).
          </p>
        </div>
        <Link
          href="/licitacoes"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          ← Voltar
        </Link>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Total" value={counts.total} tone="slate" />
        <StatCard label="Vigentes" value={counts.vigente} tone="emerald" />
        <StatCard label="Vencendo (≤30d)" value={counts.vencendo} tone="amber" />
        <StatCard label="Vencidas" value={counts.vencido} tone="rose" />
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="mb-3 text-sm font-semibold text-slate-800">
          Cadastrar certidão / atestado
        </h2>
        <form
          action={createCertidao}
          className="grid grid-cols-1 gap-3 md:grid-cols-2"
        >
          <label className="flex flex-col text-xs font-medium text-slate-600">
            CNPJ da empresa <span className="text-rose-500">*</span>
            <input
              name="empresa_cnpj"
              required
              maxLength={32}
              placeholder="44.229.813/0001-00"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Tipo <span className="text-rose-500">*</span>
            <select
              name="tipo"
              required
              defaultValue=""
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="" disabled>
                — selecione —
              </option>
              {TIPOS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Número
            <input
              name="numero"
              maxLength={128}
              placeholder="ex: ABC-2026-001"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Órgão emissor
            <input
              name="orgao_emissor"
              maxLength={255}
              placeholder="ex: Receita Federal"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Emissão
            <input
              name="emissao"
              type="date"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Validade
            <input
              name="validade"
              type="date"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600 md:col-span-2">
            Observações
            <textarea
              name="observacoes"
              maxLength={2048}
              rows={2}
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <div className="md:col-span-2">
            <button
              type="submit"
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
            >
              Cadastrar
            </button>
          </div>
        </form>
      </section>

      <section className="space-y-3">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-800">Cadastradas</h2>
          <form className="flex items-center gap-2 text-xs">
            <select
              name="status"
              defaultValue={params.status ?? ""}
              className="rounded-md border border-slate-300 px-2 py-1"
            >
              <option value="">Todos os status</option>
              <option value="vigente">Vigente</option>
              <option value="vencendo">Vencendo</option>
              <option value="vencido">Vencido</option>
              <option value="sem_validade">Sem validade</option>
            </select>
            <select
              name="tipo"
              defaultValue={params.tipo ?? ""}
              className="rounded-md border border-slate-300 px-2 py-1"
            >
              <option value="">Todos os tipos</option>
              {TIPOS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <input
              name="empresa_cnpj"
              defaultValue={params.empresa_cnpj ?? ""}
              placeholder="filtrar CNPJ"
              className="rounded-md border border-slate-300 px-2 py-1"
            />
            <button
              type="submit"
              className="rounded-md border border-slate-300 bg-white px-2 py-1 hover:bg-slate-50"
            >
              Filtrar
            </button>
          </form>
        </header>

        {certidoes === null ? (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            API indisponível. Suba o backend (<code>uvicorn</code>).
          </div>
        ) : certidoes.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
            Nenhuma certidão cadastrada com esses filtros. Crie a primeira no
            formulário acima.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-600">
                <tr>
                  <th className="px-4 py-2 text-left">Tipo</th>
                  <th className="px-4 py-2 text-left">CNPJ</th>
                  <th className="px-4 py-2 text-left">Número</th>
                  <th className="px-4 py-2 text-left">Validade</th>
                  <th className="px-4 py-2 text-left">Status</th>
                  <th className="px-4 py-2 text-right">Ações</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {certidoes.map((c) => {
                  const badge = statusBadge(c.status_atual);
                  return (
                    <tr key={c.id}>
                      <td className="px-4 py-2 font-medium text-slate-900">
                        {TIPO_LABEL[c.tipo] ?? c.tipo}
                        {c.orgao_emissor && (
                          <div className="text-xs text-slate-400">
                            {c.orgao_emissor}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-2 font-mono text-xs text-slate-700">
                        {c.empresa_cnpj}
                      </td>
                      <td className="px-4 py-2 text-slate-700">
                        {c.numero ?? "—"}
                      </td>
                      <td className="px-4 py-2 text-slate-700">
                        {formatDate(c.validade)}
                        {c.dias_para_vencer !== null && (
                          <div className="text-xs text-slate-400">
                            {c.dias_para_vencer < 0
                              ? `vencida há ${Math.abs(c.dias_para_vencer)} dia(s)`
                              : c.dias_para_vencer === 0
                                ? "vence hoje"
                                : `${c.dias_para_vencer} dia(s)`}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-semibold ${badge.className}`}
                        >
                          {badge.label}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right">
                        <form action={deleteCertidao} className="inline">
                          <input type="hidden" name="id" value={c.id} />
                          <button
                            type="submit"
                            className="rounded border border-rose-300 bg-white px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50"
                          >
                            Excluir
                          </button>
                        </form>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

type Tone = "slate" | "emerald" | "amber" | "rose";

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: Tone;
}) {
  const toneClasses: Record<Tone, string> = {
    slate: "bg-slate-50 border-slate-200 text-slate-900",
    emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
    amber: "bg-amber-50 border-amber-200 text-amber-900",
    rose: "bg-rose-50 border-rose-200 text-rose-900",
  };
  return (
    <div className={`rounded-xl border p-4 ${toneClasses[tone]}`}>
      <div className="text-xs font-medium uppercase opacity-70">{label}</div>
      <div className="mt-1 text-2xl font-bold">{value}</div>
    </div>
  );
}
