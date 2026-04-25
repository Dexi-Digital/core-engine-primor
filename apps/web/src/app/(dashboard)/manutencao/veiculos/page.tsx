import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type DocumentoVeiculo = {
  id: number;
  veiculo_id: number;
  tipo: string;
  numero: string | null;
  emissao: string | null;
  validade: string | null;
  valor: string | null;
  source: string;
};

type Veiculo = {
  id: number;
  placa: string;
  renavam: string | null;
  chassi: string | null;
  marca: string | null;
  modelo: string | null;
  ano_fabricacao: number | null;
  ano_modelo: number | null;
  cor: string | null;
  tipo: string | null;
  combustivel: string | null;
  obra: string | null;
  setor: string | null;
  km_atual: number | null;
  status: string;
  data_aquisicao: string | null;
  data_baixa: string | null;
  observacoes: string | null;
  documentos: DocumentoVeiculo[];
};

type VeiculoListResponse = {
  items: Veiculo[];
  total: number;
  page: number;
  page_size: number;
};

const STATUSES: Array<[string, string]> = [
  ["ativo", "Ativo"],
  ["manutencao", "Manutenção"],
  ["baixado", "Baixado"],
  ["vendido", "Vendido"],
];

const STATUS_BADGE: Record<string, string> = {
  ativo: "bg-emerald-100 text-emerald-700",
  manutencao: "bg-amber-100 text-amber-700",
  baixado: "bg-slate-200 text-slate-600",
  vendido: "bg-slate-200 text-slate-600",
};

export const dynamic = "force-dynamic";

function formatPlaca(placa: string): string {
  // Mostra com hífen para leitura -- ABC1234 -> ABC-1234, ABC1D23 -> ABC-1D23.
  if (placa.length !== 7) return placa;
  return `${placa.slice(0, 3)}-${placa.slice(3)}`;
}

async function fetchVeiculos(
  params: { status?: string; obra?: string; tipo?: string; search?: string } = {},
): Promise<VeiculoListResponse | null> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.obra) qs.set("obra", params.obra);
  if (params.tipo) qs.set("tipo", params.tipo);
  if (params.search) qs.set("search", params.search);
  const path = `/api/v1/manutencao-frota/veiculos${
    qs.toString() ? `?${qs}` : ""
  }`;
  try {
    return await apiFetch<VeiculoListResponse>(path);
  } catch {
    return null;
  }
}

async function createVeiculo(formData: FormData): Promise<void> {
  "use server";
  const placa = String(formData.get("placa") ?? "").trim();
  if (!placa) return;

  const numericOrNull = (v: FormDataEntryValue | null): number | null => {
    const s = String(v ?? "").trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  };

  const payload: Record<string, unknown> = {
    placa,
    renavam: String(formData.get("renavam") ?? "").trim() || null,
    chassi: String(formData.get("chassi") ?? "").trim() || null,
    marca: String(formData.get("marca") ?? "").trim() || null,
    modelo: String(formData.get("modelo") ?? "").trim() || null,
    ano_fabricacao: numericOrNull(formData.get("ano_fabricacao")),
    ano_modelo: numericOrNull(formData.get("ano_modelo")),
    cor: String(formData.get("cor") ?? "").trim() || null,
    tipo: String(formData.get("tipo") ?? "").trim() || null,
    combustivel: String(formData.get("combustivel") ?? "").trim() || null,
    obra: String(formData.get("obra") ?? "").trim() || null,
    setor: String(formData.get("setor") ?? "").trim() || null,
    km_atual: numericOrNull(formData.get("km_atual")),
    status: String(formData.get("status") ?? "ativo").trim() || "ativo",
    data_aquisicao:
      String(formData.get("data_aquisicao") ?? "").trim() || null,
    observacoes: String(formData.get("observacoes") ?? "").trim() || null,
  };
  await apiFetch("/api/v1/manutencao-frota/veiculos", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  revalidatePath("/manutencao/veiculos");
}

async function deleteVeiculo(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/manutencao-frota/veiculos/${id}`, {
    method: "DELETE",
  });
  revalidatePath("/manutencao/veiculos");
}

export default async function VeiculosPage({
  searchParams,
}: {
  searchParams: Promise<{
    status?: string;
    obra?: string;
    tipo?: string;
    search?: string;
  }>;
}) {
  const params = await searchParams;
  const data = await fetchVeiculos(params);
  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Frota — veículos</h1>
          <p className="mt-1 text-sm text-slate-600">
            Cadastro com validação local de placa (Mercosul/antiga), Renavam
            (DV) e chassi (ISO 3779). Documentos (CRLV, IPVA, seguro)
            armazenados com histórico. RPA Detran SP entra em PR seguinte.
          </p>
        </div>
        <Link
          href="/manutencao"
          className="text-xs text-slate-500 hover:text-slate-800"
        >
          ← voltar para Manutenção
        </Link>
      </header>

      {/* Filtros */}
      <form
        method="get"
        className="grid grid-cols-1 gap-3 rounded-lg border border-slate-200 bg-white p-4 md:grid-cols-5"
      >
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            Buscar (placa / modelo / marca)
          </span>
          <input
            name="search"
            defaultValue={params.search ?? ""}
            placeholder="ex: ABC1234, Constellation"
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
            Tipo
          </span>
          <input
            name="tipo"
            defaultValue={params.tipo ?? ""}
            placeholder="caminhao / carro / maquina"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
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
              ? "Nenhum veículo cadastrado ainda"
              : `${total} veículo(s)`}
          </h2>
        </header>
        {data === null ? (
          <p className="px-4 py-8 text-sm text-slate-500">
            Não foi possível conectar à API. Verifique se o backend está no ar.
          </p>
        ) : items.length === 0 ? (
          <p className="px-4 py-8 text-sm text-slate-500">
            Use o formulário abaixo para cadastrar o primeiro veículo.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2 text-left">Placa</th>
                <th className="px-4 py-2 text-left">Marca / Modelo</th>
                <th className="px-4 py-2 text-left">Tipo</th>
                <th className="px-4 py-2 text-left">Obra / Setor</th>
                <th className="px-4 py-2 text-right">KM</th>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">Docs</th>
                <th className="px-4 py-2 text-right">Ações</th>
              </tr>
            </thead>
            <tbody>
              {items.map((v) => (
                <tr
                  key={v.id}
                  className="border-t border-slate-100 text-slate-700"
                >
                  <td className="px-4 py-2 font-mono text-xs">
                    {formatPlaca(v.placa)}
                  </td>
                  <td className="px-4 py-2">
                    {v.marca ?? "—"}
                    {v.modelo ? (
                      <span className="ml-1 text-slate-500">{v.modelo}</span>
                    ) : null}
                    {v.ano_modelo ? (
                      <span className="ml-2 text-xs text-slate-400">
                        ({v.ano_modelo})
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 text-xs">{v.tipo ?? "—"}</td>
                  <td className="px-4 py-2">
                    {v.obra ?? "—"}
                    {v.setor ? (
                      <span className="ml-1 text-xs text-slate-400">
                        / {v.setor}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 text-right text-xs tabular-nums">
                    {v.km_atual !== null
                      ? v.km_atual.toLocaleString("pt-BR")
                      : "—"}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        STATUS_BADGE[v.status] ?? "bg-slate-100 text-slate-700"
                      }`}
                    >
                      {v.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-500">
                    {v.documentos.length > 0
                      ? `${v.documentos.length}`
                      : "—"}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <form action={deleteVeiculo} className="inline">
                      <input type="hidden" name="id" value={v.id} />
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
        <h2 className="text-sm font-semibold">Cadastrar novo veículo</h2>
        <p className="mt-1 text-xs text-slate-500">
          Apenas a placa é obrigatória. Renavam e chassi são validados
          (algoritmos locais — sem API externa).
        </p>
        <form
          action={createVeiculo}
          className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3"
        >
          <Field
            name="placa"
            label="Placa *"
            required
            placeholder="ABC1234 ou ABC1D23"
          />
          <Field name="renavam" label="Renavam" placeholder="11 dígitos" />
          <Field
            name="chassi"
            label="Chassi"
            placeholder="17 alfanuméricos (ISO 3779)"
          />

          <Field name="marca" label="Marca" placeholder="VW, Mercedes…" />
          <Field name="modelo" label="Modelo" />
          <Field name="cor" label="Cor" />

          <Field
            name="ano_fabricacao"
            label="Ano de fabricação"
            type="number"
          />
          <Field name="ano_modelo" label="Ano modelo" type="number" />
          <Field
            name="tipo"
            label="Tipo"
            placeholder="caminhão / carro / máquina"
          />

          <Field
            name="combustivel"
            label="Combustível"
            placeholder="diesel / flex / GNV"
          />
          <Field name="obra" label="Obra alocada" />
          <Field name="setor" label="Setor" />

          <Field name="km_atual" label="KM atual" type="number" />
          <Field name="data_aquisicao" label="Data de aquisição" type="date" />
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Status
            </span>
            <select
              name="status"
              defaultValue="ativo"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              {STATUSES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>

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
              Cadastrar veículo
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
