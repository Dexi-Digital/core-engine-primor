import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type EmpregoAnterior = {
  id: number;
  empresa_cnpj: string | null;
  empresa_razao_social: string | null;
  cargo: string | null;
  inicio: string | null;
  fim: string | null;
};

type Employee = {
  id: number;
  cpf: string;
  matricula: string | null;
  nome_completo: string;
  cargo: string;
  obra: string | null;
  setor: string | null;
  status: string;
  source: string;
  data_admissao: string | null;
  data_nascimento: string | null;
  telefone: string | null;
  email: string | null;
  cep: string | null;
  logradouro: string | null;
  numero: string | null;
  bairro: string | null;
  cidade: string | null;
  uf: string | null;
  aso_data: string | null;
  aso_validade: string | null;
  aso_status: string | null;
  is_motorista: boolean;
  is_operador_maquina: boolean;
  is_admin_office: boolean;
  is_alturas: boolean;
  is_eletricista: boolean;
  empregos_anteriores: EmpregoAnterior[];
};

type EmployeeListResponse = {
  items: Employee[];
  total: number;
  limit: number;
  offset: number;
};

const STATUSES: Array<[string, string]> = [
  ["ativo", "Ativo"],
  ["afastado", "Afastado"],
  ["desligado", "Desligado"],
];

export const dynamic = "force-dynamic";

function formatCpf(cpf: string): string {
  const n = cpf.replace(/\D/g, "");
  if (n.length !== 11) return cpf;
  return `${n.slice(0, 3)}.${n.slice(3, 6)}.${n.slice(6, 9)}-${n.slice(9)}`;
}

const STATUS_BADGE: Record<string, string> = {
  ativo: "bg-emerald-100 text-emerald-700",
  afastado: "bg-amber-100 text-amber-700",
  desligado: "bg-slate-200 text-slate-600",
};

// Cores das badges de ASO. Convencao mesma do D.6 (cendoes):
// vencido = vermelho, vencendo = ambar, vigente = verde, sem_validade = neutro.
const ASO_BADGE: Record<string, string> = {
  vigente: "bg-emerald-100 text-emerald-700",
  vencendo: "bg-amber-100 text-amber-700",
  vencido: "bg-red-100 text-red-700",
  sem_validade: "bg-slate-100 text-slate-500",
};

const ASO_FILTROS: Array<[string, string]> = [
  ["vigente", "Vigente"],
  ["vencendo", "Vencendo (<=30d)"],
  ["vencido", "Vencido"],
  ["sem_validade", "Sem validade"],
];

function asoBadgeLabel(emp: Employee): string {
  const status = emp.aso_status ?? "sem_validade";
  if (status === "sem_validade" || !emp.aso_validade) return "sem ASO";
  // Calcula dias restantes; se ja vencido, mostra "vencido X d".
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const validade = new Date(`${emp.aso_validade}T00:00:00`);
  const days = Math.round(
    (validade.getTime() - today.getTime()) / (1000 * 60 * 60 * 24),
  );
  if (status === "vencido") return `vencido ha ${Math.abs(days)}d`;
  if (status === "vencendo") return `vence em ${days}d`;
  return "vigente";
}

async function fetchEmployees(
  params: {
    status?: string;
    obra?: string;
    setor?: string;
    is_admin_office?: string;
    search?: string;
    aso_status?: string;
  } = {},
): Promise<EmployeeListResponse | null> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.obra) qs.set("obra", params.obra);
  if (params.setor) qs.set("setor", params.setor);
  if (params.is_admin_office) qs.set("is_admin_office", params.is_admin_office);
  if (params.search) qs.set("search", params.search);
  if (params.aso_status) qs.set("aso_status", params.aso_status);
  const path = `/api/v1/dp-sesmt/employees${qs.toString() ? `?${qs}` : ""}`;
  try {
    return await apiFetch<EmployeeListResponse>(path);
  } catch {
    return null;
  }
}

// Setores conhecidos do organograma do dossie. UI cai em texto livre se
// o setor existente nao casar (o filtro do backend usa `ilike`).
const SETORES_CONHECIDOS = [
  "Diretoria",
  "Planejamento",
  "Licitacoes",
  "Contratos",
  "Orcamento",
  "Comercial",
  "TI",
  "Administrativo",
  "Consultoria",
];

const TIPO_FILTROS: Array<[string, string]> = [
  ["true", "Equipe administrativa"],
  ["false", "Operacional de obra"],
];

async function createEmployee(formData: FormData): Promise<void> {
  "use server";
  const cpf = String(formData.get("cpf") ?? "").trim();
  const nome_completo = String(formData.get("nome_completo") ?? "").trim();
  const cargo = String(formData.get("cargo") ?? "").trim();
  if (!cpf || !nome_completo || !cargo) return;

  const payload: Record<string, unknown> = {
    cpf,
    nome_completo,
    cargo,
    matricula: String(formData.get("matricula") ?? "").trim() || null,
    obra: String(formData.get("obra") ?? "").trim() || null,
    setor: String(formData.get("setor") ?? "").trim() || null,
    tipo_contrato: String(formData.get("tipo_contrato") ?? "").trim() || null,
    data_admissao:
      String(formData.get("data_admissao") ?? "").trim() || null,
    data_nascimento:
      String(formData.get("data_nascimento") ?? "").trim() || null,
    telefone: String(formData.get("telefone") ?? "").trim() || null,
    email: String(formData.get("email") ?? "").trim() || null,
    cep: String(formData.get("cep") ?? "").trim() || null,
    logradouro: String(formData.get("logradouro") ?? "").trim() || null,
    numero: String(formData.get("numero") ?? "").trim() || null,
    bairro: String(formData.get("bairro") ?? "").trim() || null,
    cidade: String(formData.get("cidade") ?? "").trim() || null,
    uf: String(formData.get("uf") ?? "").trim() || null,
    aso_data: String(formData.get("aso_data") ?? "").trim() || null,
    aso_validade: String(formData.get("aso_validade") ?? "").trim() || null,
    aso_resultado:
      String(formData.get("aso_resultado") ?? "").trim() || null,
    is_motorista: formData.get("is_motorista") === "on",
    is_operador_maquina: formData.get("is_operador_maquina") === "on",
    is_admin_office: formData.get("is_admin_office") === "on",
    is_alturas: formData.get("is_alturas") === "on",
    is_eletricista: formData.get("is_eletricista") === "on",
    observacoes: String(formData.get("observacoes") ?? "").trim() || null,
  };
  await apiFetch("/api/v1/dp-sesmt/employees", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  revalidatePath("/rh/funcionarios");
}

async function deleteEmployee(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/dp-sesmt/employees/${id}`, { method: "DELETE" });
  revalidatePath("/rh/funcionarios");
}

export default async function FuncionariosPage({
  searchParams,
}: {
  searchParams: Promise<{
    status?: string;
    obra?: string;
    setor?: string;
    is_admin_office?: string;
    search?: string;
    aso_status?: string;
  }>;
}) {
  const params = await searchParams;
  const data = await fetchEmployees(params);
  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Funcionários</h1>
          <p className="mt-1 text-sm text-slate-600">
            Cadastro manual + dossiê de admissão por APIs públicas (ViaCEP /
            BrasilAPI). Sync com Domínio/Onvio entra quando as credenciais
            chegarem.
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
        className="grid grid-cols-1 gap-3 rounded-lg border border-slate-200 bg-white p-4 md:grid-cols-3 xl:grid-cols-4"
      >
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            Buscar (nome / CPF / cargo)
          </span>
          <input
            name="search"
            defaultValue={params.search ?? ""}
            placeholder="ex: Joao, 111.444, Pedreiro"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
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
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            Obra
          </span>
          <input
            name="obra"
            defaultValue={params.obra ?? ""}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            Setor
          </span>
          <select
            name="setor"
            defaultValue={params.setor ?? ""}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">— todos —</option>
            {SETORES_CONHECIDOS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            Tipo
          </span>
          <select
            name="is_admin_office"
            defaultValue={params.is_admin_office ?? ""}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">— todos —</option>
            {TIPO_FILTROS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            ASO
          </span>
          <select
            name="aso_status"
            defaultValue={params.aso_status ?? ""}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">— todos —</option>
            {ASO_FILTROS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-end">
          <button
            type="submit"
            className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700"
          >
            Filtrar
          </button>
        </div>
      </form>

      {/* Lista */}
      <section className="rounded-lg border border-slate-200 bg-white">
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold">
            {total === 0
              ? "Nenhum funcionário cadastrado ainda"
              : `${total} funcionário(s)`}
          </h2>
        </header>
        {data === null ? (
          <p className="px-4 py-8 text-sm text-slate-500">
            Não foi possível conectar à API. Verifique se o backend está no ar.
          </p>
        ) : items.length === 0 ? (
          <p className="px-4 py-8 text-sm text-slate-500">
            Use o formulário abaixo para cadastrar o primeiro funcionário.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2 text-left">Nome</th>
                <th className="px-4 py-2 text-left">CPF</th>
                <th className="px-4 py-2 text-left">Cargo</th>
                <th className="px-4 py-2 text-left">Obra / Setor</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">ASO</th>
                <th className="px-4 py-2 text-left">Admissão</th>
                <th className="px-4 py-2 text-right">Ações</th>
              </tr>
            </thead>
            <tbody>
              {items.map((emp) => (
                <tr
                  key={emp.id}
                  className="border-t border-slate-100 text-slate-700"
                >
                  <td className="px-4 py-2 font-medium">
                    {emp.nome_completo}
                    {emp.matricula ? (
                      <span className="ml-2 text-xs text-slate-400">
                        #{emp.matricula}
                      </span>
                    ) : null}
                    {emp.is_admin_office ? (
                      <span
                        className="ml-2 inline-flex items-center rounded-full bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-700"
                        title="Equipe administrativa (organograma do dossie)"
                      >
                        adm
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {formatCpf(emp.cpf)}
                  </td>
                  <td className="px-4 py-2">{emp.cargo}</td>
                  <td className="px-4 py-2">
                    {emp.obra ?? "—"}
                    {emp.setor ? (
                      <span className="ml-1 text-xs text-slate-400">
                        / {emp.setor}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        STATUS_BADGE[emp.status] ?? "bg-slate-100 text-slate-700"
                      }`}
                    >
                      {emp.status}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        ASO_BADGE[emp.aso_status ?? "sem_validade"] ??
                        "bg-slate-100 text-slate-500"
                      }`}
                      title={emp.aso_validade ?? undefined}
                    >
                      {asoBadgeLabel(emp)}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {emp.data_admissao ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <form action={deleteEmployee} className="inline">
                      <input type="hidden" name="id" value={emp.id} />
                      <button
                        type="submit"
                        className="text-xs text-red-600 hover:text-red-800"
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

      {/* Form de cadastro */}
      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="text-sm font-semibold">Cadastrar novo funcionário</h2>
        <p className="mt-1 text-xs text-slate-500">
          Campos com * são obrigatórios. CPF é validado pelos dígitos
          verificadores antes de ser persistido.
        </p>
        <form
          action={createEmployee}
          className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3"
        >
          <Field name="cpf" label="CPF *" required placeholder="000.000.000-00" />
          <Field name="nome_completo" label="Nome completo *" required />
          <Field name="matricula" label="Matrícula" />

          <Field name="cargo" label="Cargo *" required />
          <Field name="obra" label="Obra" />
          <Field name="setor" label="Setor" />

          <Field
            name="tipo_contrato"
            label="Tipo de contrato"
            placeholder="CLT / PJ / Estágio"
          />
          <Field name="data_admissao" label="Data de admissão" type="date" />
          <Field name="data_nascimento" label="Data de nascimento" type="date" />

          <Field name="telefone" label="Telefone" />
          <Field name="email" label="E-mail" type="email" />
          <Field name="cep" label="CEP" placeholder="00000-000" />

          <Field name="logradouro" label="Logradouro" />
          <Field name="numero" label="Número" />
          <Field name="bairro" label="Bairro" />

          <Field name="cidade" label="Cidade" />
          <Field name="uf" label="UF" />

          <Field name="aso_data" label="ASO - data do exame" type="date" />
          <Field name="aso_validade" label="ASO - validade" type="date" />
          <Field
            name="aso_resultado"
            label="ASO - resultado"
            placeholder="apto / inapto / apto_restricoes"
          />

          <fieldset className="md:col-span-3 rounded border border-slate-200 p-3">
            <legend className="px-1 text-xs font-medium uppercase tracking-wide text-slate-500">
              Flags SST (disparam regras condicionais no diagnóstico)
            </legend>
            <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
              <label className="flex items-center gap-2">
                <input type="checkbox" name="is_motorista" />
                <span>Motorista (exige toxicológico)</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" name="is_operador_maquina" />
                <span>Operador de máquina (exige NR-12)</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" name="is_alturas" />
                <span>Trabalho em altura (exige NR-35)</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" name="is_eletricista" />
                <span>Eletricista (exige NR-10)</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" name="is_admin_office" />
                <span>Administrativo (dispensa NR-18)</span>
              </label>
            </div>
          </fieldset>

          <label className="md:col-span-3 flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Observações
            </span>
            <textarea
              name="observacoes"
              rows={2}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>

          <div className="md:col-span-3 flex justify-end">
            <button
              type="submit"
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700"
            >
              Cadastrar funcionário
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function Field({
  name,
  label,
  type = "text",
  required = false,
  placeholder,
}: {
  name: string;
  label: string;
  type?: string;
  required?: boolean;
  placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span className="font-medium uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <input
        name={name}
        type={type}
        required={required}
        placeholder={placeholder}
        className="rounded border border-slate-300 px-2 py-1 text-sm"
      />
    </label>
  );
}
