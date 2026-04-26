import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type Afastamento = {
  id: number;
  employee_id: number;
  beneficio_tipo: string;
  numero_beneficio: string | null;
  cid: string | null;
  data_inicio: string;
  dcb: string | null;
  data_pericia: string | null;
  data_retorno: string | null;
  status: string;
  observacoes: string | null;
  created_at: string;
  updated_at: string;
  dcb_status: string | null;
  dias_para_dcb: number | null;
  pericia_status: string | null;
  dias_para_pericia: number | null;
};

type Employee = {
  id: number;
  cpf: string;
  nome_completo: string;
  cargo: string;
};

const BENEFICIOS: Array<[string, string]> = [
  ["B31", "B31 — Auxilio-doenca"],
  ["B91", "B91 — Auxilio-acidente"],
  ["B32", "B32 — Aposentadoria por invalidez"],
  ["OUTRO", "Outro"],
];

const STATUSES: Array<[string, string]> = [
  ["em_andamento", "Em andamento"],
  ["encerrado", "Encerrado"],
  ["reabilitado", "Reabilitado"],
];

const STATUS_BADGE: Record<string, string> = {
  em_andamento: "bg-amber-100 text-amber-700",
  encerrado: "bg-slate-200 text-slate-600",
  reabilitado: "bg-emerald-100 text-emerald-700",
};

const PRAZO_BADGE: Record<string, string> = {
  vigente: "bg-emerald-100 text-emerald-700",
  vencendo: "bg-amber-100 text-amber-700",
  vencido: "bg-red-100 text-red-700",
};

export const dynamic = "force-dynamic";

function formatDate(d: string | null): string {
  if (!d) return "—";
  const [y, m, day] = d.split("-");
  return `${day}/${m}/${y}`;
}

function prazoLabel(
  status: string | null,
  dias: number | null,
  rotulo: string,
): string {
  if (status === null || dias === null) return `sem ${rotulo.toLowerCase()}`;
  if (status === "vencido") return `${rotulo} ha ${Math.abs(dias)}d`;
  if (status === "vencendo") return `${rotulo} em ${dias}d`;
  return `${rotulo} em ${dias}d`;
}

async function fetchAfastamentos(
  params: { status?: string } = {},
): Promise<Afastamento[]> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  const path = `/api/v1/dp-sesmt/afastamentos${qs.toString() ? `?${qs}` : ""}`;
  try {
    return await apiFetch<Afastamento[]>(path);
  } catch {
    return [];
  }
}

async function fetchEmployees(): Promise<Employee[]> {
  try {
    const resp = await apiFetch<{ items: Employee[] }>(
      "/api/v1/dp-sesmt/employees?limit=200",
    );
    return resp.items ?? [];
  } catch {
    return [];
  }
}

async function createAfastamento(formData: FormData): Promise<void> {
  "use server";
  const employee_id = Number(formData.get("employee_id"));
  const beneficio_tipo = String(formData.get("beneficio_tipo") ?? "B31");
  const data_inicio = String(formData.get("data_inicio") ?? "").trim();
  if (!employee_id || !data_inicio) return;

  const payload: Record<string, unknown> = {
    employee_id,
    beneficio_tipo,
    data_inicio,
    numero_beneficio:
      String(formData.get("numero_beneficio") ?? "").trim() || null,
    cid: String(formData.get("cid") ?? "").trim() || null,
    dcb: String(formData.get("dcb") ?? "").trim() || null,
    data_pericia: String(formData.get("data_pericia") ?? "").trim() || null,
    data_retorno: String(formData.get("data_retorno") ?? "").trim() || null,
    status: String(formData.get("status") ?? "em_andamento"),
    observacoes: String(formData.get("observacoes") ?? "").trim() || null,
  };
  await apiFetch("/api/v1/dp-sesmt/afastamentos", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  revalidatePath("/rh/afastamentos");
}

async function deleteAfastamento(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/dp-sesmt/afastamentos/${id}`, { method: "DELETE" });
  revalidatePath("/rh/afastamentos");
}

async function encerrarAfastamento(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  const data_retorno = String(formData.get("data_retorno") ?? "").trim();
  if (!id || !data_retorno) return;
  await apiFetch(`/api/v1/dp-sesmt/afastamentos/${id}`, {
    method: "PUT",
    body: JSON.stringify({ status: "encerrado", data_retorno }),
  });
  revalidatePath("/rh/afastamentos");
}

export default async function AfastamentosPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const params = await searchParams;
  const [afastamentos, employees] = await Promise.all([
    fetchAfastamentos(params),
    fetchEmployees(),
  ]);
  const empById = new Map(employees.map((e) => [e.id, e]));

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Afastamentos INSS</h1>
          <p className="mt-1 text-sm text-slate-600">
            Acompanhamento de auxilio-doenca, auxilio-acidente e aposentadoria
            por invalidez. Alertas automaticos por email para DCB (30/15/7/0d)
            e pericia medica (15/7/0d).
          </p>
        </div>
        <Link
          href="/rh"
          className="text-xs text-slate-500 hover:text-slate-800"
        >
          ← voltar para RH
        </Link>
      </header>

      {/* Filtros */}
      <form
        method="get"
        className="grid grid-cols-1 gap-3 rounded-lg border border-slate-200 bg-white p-4 md:grid-cols-4"
      >
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            Status
          </span>
          <select
            name="status"
            defaultValue={params.status ?? ""}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">— todos —</option>
            {STATUSES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="self-end rounded bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
        >
          Filtrar
        </button>
      </form>

      {/* Lista */}
      <section className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50">
            <tr className="text-xs uppercase tracking-wide text-slate-500">
              <th className="px-3 py-2">Funcionario</th>
              <th className="px-3 py-2">Beneficio</th>
              <th className="px-3 py-2">Inicio</th>
              <th className="px-3 py-2">DCB</th>
              <th className="px-3 py-2">Pericia</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2 text-right">Acoes</th>
            </tr>
          </thead>
          <tbody>
            {afastamentos.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-slate-500">
                  Nenhum afastamento cadastrado.
                </td>
              </tr>
            )}
            {afastamentos.map((a) => {
              const emp = empById.get(a.employee_id);
              return (
                <tr
                  key={a.id}
                  className="border-b border-slate-100 last:border-0"
                >
                  <td className="px-3 py-2">
                    <div className="font-medium">
                      {emp?.nome_completo ?? `#${a.employee_id}`}
                    </div>
                    <div className="text-xs text-slate-500">
                      {emp?.cargo ?? ""}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <div>{a.beneficio_tipo}</div>
                    {a.numero_beneficio && (
                      <div className="text-xs text-slate-500">
                        nº {a.numero_beneficio}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">{formatDate(a.data_inicio)}</td>
                  <td className="px-3 py-2">
                    <div>{formatDate(a.dcb)}</div>
                    {a.dcb_status && (
                      <span
                        className={`mt-1 inline-block rounded px-1.5 py-0.5 text-[10px] uppercase ${PRAZO_BADGE[a.dcb_status] ?? ""}`}
                      >
                        {prazoLabel(a.dcb_status, a.dias_para_dcb, "DCB")}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div>{formatDate(a.data_pericia)}</div>
                    {a.pericia_status && (
                      <span
                        className={`mt-1 inline-block rounded px-1.5 py-0.5 text-[10px] uppercase ${PRAZO_BADGE[a.pericia_status] ?? ""}`}
                      >
                        {prazoLabel(
                          a.pericia_status,
                          a.dias_para_pericia,
                          "Pericia",
                        )}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[a.status] ?? ""}`}
                    >
                      {a.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex justify-end gap-2">
                      {a.status === "em_andamento" && (
                        <form action={encerrarAfastamento}>
                          <input type="hidden" name="id" value={a.id} />
                          <input
                            type="date"
                            name="data_retorno"
                            required
                            className="rounded border border-slate-300 px-1.5 py-0.5 text-xs"
                          />
                          <button
                            type="submit"
                            className="ml-1 rounded bg-emerald-600 px-2 py-1 text-xs text-white hover:bg-emerald-700"
                          >
                            Encerrar
                          </button>
                        </form>
                      )}
                      <form action={deleteAfastamento}>
                        <input type="hidden" name="id" value={a.id} />
                        <button
                          type="submit"
                          className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                        >
                          Excluir
                        </button>
                      </form>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {/* Form: novo afastamento */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Cadastrar novo afastamento</h2>
        <form
          action={createAfastamento}
          className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3"
        >
          <label className="flex flex-col gap-1 text-xs md:col-span-2">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Funcionario *
            </span>
            <select
              name="employee_id"
              required
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="">— selecione —</option>
              {employees.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.nome_completo} ({e.cpf})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Tipo de benefício *
            </span>
            <select
              name="beneficio_tipo"
              required
              defaultValue="B31"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              {BENEFICIOS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Numero do beneficio
            </span>
            <input
              name="numero_beneficio"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              CID
            </span>
            <input
              name="cid"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Data início *
            </span>
            <input
              type="date"
              name="data_inicio"
              required
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              DCB (Data prevista de cessacao)
            </span>
            <input
              type="date"
              name="dcb"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Data da pericia
            </span>
            <input
              type="date"
              name="data_pericia"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs md:col-span-3">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Observacoes
            </span>
            <textarea
              name="observacoes"
              rows={2}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <div className="md:col-span-3">
            <button
              type="submit"
              className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
            >
              Cadastrar afastamento
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
