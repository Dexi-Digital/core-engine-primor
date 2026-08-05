import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type Contrato = {
  id: number;
  titulo: string;
  contraparte_nome: string;
  contraparte_documento: string | null;
  tipo: string;
  obra_id: number | null;
  // Numeric(20,2) -- Pydantic serializa Decimal como string (mesma
  // convencao de valor_total_estimado em licitacoes/page.tsx). NAO
  // converter para centavos (o brief original assumia int; o schema
  // real (`app/modules/financeiro_contratos/schemas.py`) usa Decimal).
  valor: string | null;
  data_inicio: string;
  data_fim: string | null;
  status: string;
  arquivo_path: string | null;
  easyjur_ref: string | null;
  observacoes: string | null;
  vencimento_status: string | null;
  dias_para_vencer: number | null;
  created_at: string;
  updated_at: string;
};

// Espelha TIPOS_CONTRATO / STATUS_CONTRATO do backend (duplicado de
// proposito -- mesmo racional da pagina de certidoes).
const TIPOS: Array<[string, string]> = [
  ["cliente", "Contrato com cliente"],
  ["fornecedor", "Contrato com fornecedor"],
  ["locacao", "Locação de equipamento"],
];
const STATUS: Array<[string, string]> = [
  ["rascunho", "Rascunho"],
  ["vigente", "Vigente"],
  ["encerrado", "Encerrado"],
  ["judicializado", "Judicializado"],
];

export const dynamic = "force-dynamic";

async function fetchContratos(
  params: { status?: string; tipo?: string } = {},
): Promise<Contrato[] | null> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.tipo) qs.set("tipo", params.tipo);
  const path = `/api/v1/financeiro/contratos${qs.toString() ? `?${qs}` : ""}`;
  try {
    return await apiFetch<Contrato[]>(path);
  } catch {
    return null;
  }
}

async function createContrato(formData: FormData): Promise<void> {
  "use server";
  const titulo = String(formData.get("titulo") ?? "").trim();
  const contraparte_nome = String(formData.get("contraparte_nome") ?? "").trim();
  const tipo = String(formData.get("tipo") ?? "").trim();
  const data_inicio = String(formData.get("data_inicio") ?? "").trim();
  if (!titulo || !contraparte_nome || !tipo || !data_inicio) return;
  const valorReais = String(formData.get("valor") ?? "").trim();
  const payload: Record<string, unknown> = {
    titulo,
    contraparte_nome,
    tipo,
    data_inicio,
    contraparte_documento:
      String(formData.get("contraparte_documento") ?? "").trim() || null,
    // Decimal no backend (Numeric(20,2)) -- envia string em reais com
    // ponto decimal; o input aceita "," (pt-BR) e normalizamos aqui.
    valor: valorReais ? valorReais.replace(",", ".") : null,
    data_fim: String(formData.get("data_fim") ?? "").trim() || null,
    status: String(formData.get("status") ?? "rascunho").trim(),
    easyjur_ref: String(formData.get("easyjur_ref") ?? "").trim() || null,
    observacoes: String(formData.get("observacoes") ?? "").trim() || null,
  };
  try {
    await apiFetch("/api/v1/financeiro/contratos", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error("[financeiro/contratos] create failed", err);
  }
  revalidatePath("/financeiro/contratos");
}

async function encerrarContrato(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/financeiro/contratos/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "encerrado" }),
    });
  } catch (err) {
    console.error("[financeiro/contratos] encerrar failed", err);
  }
  revalidatePath("/financeiro/contratos");
}

async function deleteContrato(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/financeiro/contratos/${id}`, { method: "DELETE" });
  } catch (err) {
    console.error("[financeiro/contratos] delete failed", err);
  }
  revalidatePath("/financeiro/contratos");
}

function vencimentoBadge(c: Contrato): { label: string; className: string } {
  if (c.status === "encerrado")
    return { label: "Encerrado", className: "bg-slate-100 text-slate-700" };
  switch (c.vencimento_status) {
    case "vigente":
      return { label: "Vigente", className: "bg-emerald-100 text-emerald-800" };
    case "vencendo":
      return {
        label: `Vence em ${c.dias_para_vencer}d`,
        className: "bg-amber-100 text-amber-800",
      };
    case "vencido":
      return { label: "Vencido", className: "bg-rose-100 text-rose-800" };
    case "sem_validade":
      return { label: "Sem prazo", className: "bg-slate-100 text-slate-700" };
    default:
      return { label: "—", className: "bg-slate-100 text-slate-700" };
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  if (!y || !m || !d) return iso;
  return `${d}/${m}/${y}`;
}

function formatValor(valor: string | null): string {
  if (valor == null) return "—";
  const num = Number(valor);
  if (Number.isNaN(num)) return valor;
  return num.toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
}

type SearchParams = { status?: string; tipo?: string };

export default async function ContratosPage(props: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await props.searchParams;
  const contratos = await fetchContratos(params);

  const counts = {
    total: contratos?.length ?? 0,
    vigente:
      contratos?.filter(
        (c) => c.status !== "encerrado" && c.vencimento_status === "vigente",
      ).length ?? 0,
    vencendo:
      contratos?.filter(
        (c) => c.status !== "encerrado" && c.vencimento_status === "vencendo",
      ).length ?? 0,
    vencido:
      contratos?.filter(
        (c) => c.status !== "encerrado" && c.vencimento_status === "vencido",
      ).length ?? 0,
  };

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Contratos</h1>
          <p className="mt-1 text-sm text-slate-500">
            Ciclo de contratos (cliente, fornecedor, locação) com alerta
            automático de vencimento por email (30, 15, 7 e 0 dias antes).
          </p>
        </div>
        <Link
          href="/financeiro"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          ← Voltar
        </Link>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {(
          [
            ["Total", counts.total, "border-slate-200"],
            ["Vigentes", counts.vigente, "border-emerald-200"],
            ["Vencendo (≤30d)", counts.vencendo, "border-amber-300"],
            ["Vencidos", counts.vencido, "border-rose-300"],
          ] as Array<[string, number, string]>
        ).map(([label, value, border]) => (
          <div
            key={label}
            className={`rounded-xl border ${border} bg-white p-4`}
          >
            <p className="text-xs font-medium text-slate-500">{label}</p>
            <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
          </div>
        ))}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="mb-3 text-sm font-semibold text-slate-800">
          Cadastrar contrato
        </h2>
        <form
          action={createContrato}
          className="grid grid-cols-1 gap-3 md:grid-cols-2"
        >
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Título <span className="text-rose-500">*</span>
            <input
              name="titulo"
              required
              maxLength={255}
              placeholder="Locação de escavadeira CAT 320"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Contraparte <span className="text-rose-500">*</span>
            <input
              name="contraparte_nome"
              required
              maxLength={255}
              placeholder="TratorMax Ltda"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            CNPJ/CPF da contraparte
            <input
              name="contraparte_documento"
              maxLength={32}
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
            Valor (R$)
            <input
              name="valor"
              inputMode="decimal"
              placeholder="15000,00"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Status
            <select
              name="status"
              defaultValue="vigente"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            >
              {STATUS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Início <span className="text-rose-500">*</span>
            <input
              name="data_inicio"
              type="date"
              required
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Fim (vazio = prazo indeterminado)
            <input
              name="data_fim"
              type="date"
              className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col text-xs font-medium text-slate-600">
            Ref. EasyJur (se judicializado)
            <input
              name="easyjur_ref"
              maxLength={128}
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
              className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              Cadastrar
            </button>
          </div>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-800">
            Contratos cadastrados
          </h2>
          <form className="flex items-center gap-2 text-xs">
            <label className="flex items-center gap-1 text-slate-500">
              Status
              <select
                name="status"
                defaultValue={params.status ?? ""}
                className="rounded-md border border-slate-300 px-2 py-1 text-slate-700"
              >
                <option value="">Todos os status</option>
                {STATUS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1 text-slate-500">
              Tipo
              <select
                name="tipo"
                defaultValue={params.tipo ?? ""}
                className="rounded-md border border-slate-300 px-2 py-1 text-slate-700"
              >
                <option value="">Todos os tipos</option>
                {TIPOS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              className="rounded-md border border-slate-300 bg-white px-3 py-1 font-medium text-slate-700 hover:bg-slate-50"
            >
              Filtrar
            </button>
          </form>
        </div>
        {!contratos || contratos.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">
            {contratos === null
              ? "Não foi possível carregar os contratos (API fora do ar?)."
              : "Nenhum contrato cadastrado ainda."}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs text-slate-500">
                  <th className="py-2 pr-3">Título</th>
                  <th className="py-2 pr-3">Contraparte</th>
                  <th className="py-2 pr-3">Tipo</th>
                  <th className="py-2 pr-3">Valor</th>
                  <th className="py-2 pr-3">Fim</th>
                  <th className="py-2 pr-3">Vencimento</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {contratos.map((c) => {
                  const badge = vencimentoBadge(c);
                  return (
                    <tr key={c.id} className="border-b border-slate-100">
                      <td className="py-2 pr-3 font-medium text-slate-800">
                        {c.titulo}
                        {c.easyjur_ref ? (
                          <span className="ml-2 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-semibold text-violet-700">
                            EasyJur {c.easyjur_ref}
                          </span>
                        ) : null}
                      </td>
                      <td className="py-2 pr-3">{c.contraparte_nome}</td>
                      <td className="py-2 pr-3">
                        {TIPOS.find(([v]) => v === c.tipo)?.[1] ?? c.tipo}
                      </td>
                      <td className="py-2 pr-3">{formatValor(c.valor)}</td>
                      <td className="py-2 pr-3">{formatDate(c.data_fim)}</td>
                      <td className="py-2 pr-3">
                        <span
                          className={`rounded px-2 py-0.5 text-xs font-semibold ${badge.className}`}
                        >
                          {badge.label}
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        {STATUS.find(([v]) => v === c.status)?.[1] ?? c.status}
                      </td>
                      <td className="py-2 text-right">
                        {c.status !== "encerrado" ? (
                          <form action={encerrarContrato} className="inline">
                            <input type="hidden" name="id" value={c.id} />
                            <button
                              type="submit"
                              className="mr-2 text-xs font-medium text-slate-500 hover:text-slate-800"
                            >
                              Encerrar
                            </button>
                          </form>
                        ) : null}
                        <form action={deleteContrato} className="inline">
                          <input type="hidden" name="id" value={c.id} />
                          <button
                            type="submit"
                            className="text-xs font-medium text-rose-500 hover:text-rose-700"
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
