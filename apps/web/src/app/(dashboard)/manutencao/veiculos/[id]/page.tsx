import Link from "next/link";
import { notFound } from "next/navigation";
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
  observacoes: string | null;
};

type Veiculo = {
  id: number;
  placa: string;
  renavam: string | null;
  chassi: string | null;
  marca: string | null;
  modelo: string | null;
  ano_modelo: number | null;
  cor: string | null;
  tipo: string | null;
  obra: string | null;
  km_atual: number | null;
  status: string;
  documentos: DocumentoVeiculo[];
};

type ConsultaPayload = {
  marca_modelo?: string | null;
  ano_modelo?: number | null;
  cor?: string | null;
  combustivel?: string | null;
  situacao?: string | null;
  ipva?: {
    exercicio?: number | null;
    vencimento?: string | null;
    pago?: boolean | null;
    valor?: string | null;
  } | null;
  licenciamento?: {
    exercicio?: number | null;
    vencimento?: string | null;
    pago?: boolean | null;
    valor?: string | null;
  } | null;
  multas?: Array<{
    auto?: string | null;
    data?: string | null;
    valor?: string | null;
    descricao?: string | null;
  }> | null;
  restricoes?: string[] | null;
};

type Consulta = {
  id: number;
  veiculo_id: number;
  placa: string;
  uf: string;
  status: string;
  source: string;
  payload: ConsultaPayload | null;
  error_msg: string | null;
  executed_at: string;
};

type ConsultaListResponse = {
  items: Consulta[];
  total: number;
};

const STATUS_BADGE: Record<string, string> = {
  ativo: "bg-emerald-100 text-emerald-700",
  manutencao: "bg-amber-100 text-amber-700",
  baixado: "bg-slate-200 text-slate-600",
  vendido: "bg-slate-200 text-slate-600",
};

const CONSULTA_BADGE: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-700",
  mock: "bg-blue-100 text-blue-700",
  erro: "bg-red-100 text-red-700",
  pendente: "bg-amber-100 text-amber-700",
};

const SITUACAO_BADGE: Record<string, string> = {
  REGULAR: "bg-emerald-100 text-emerald-700",
  BLOQUEADO: "bg-red-100 text-red-700",
  "ROUBO/FURTO": "bg-red-200 text-red-900",
};

export const dynamic = "force-dynamic";

function formatPlaca(placa: string): string {
  if (placa.length !== 7) return placa;
  return `${placa.slice(0, 3)}-${placa.slice(3)}`;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("pt-BR");
  } catch {
    return iso;
  }
}

function fmtDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

async function fetchVeiculo(id: number): Promise<Veiculo | null> {
  try {
    return await apiFetch<Veiculo>(
      `/api/v1/manutencao-frota/veiculos/${id}`,
    );
  } catch {
    return null;
  }
}

async function fetchConsultas(id: number): Promise<ConsultaListResponse> {
  try {
    return await apiFetch<ConsultaListResponse>(
      `/api/v1/manutencao-frota/veiculos/${id}/consultas?page_size=20`,
    );
  } catch {
    return { items: [], total: 0 };
  }
}

async function consultarDetran(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  const uf = String(formData.get("uf") ?? "SP")
    .trim()
    .toUpperCase();
  if (!id) return;
  try {
    await apiFetch(
      `/api/v1/manutencao-frota/veiculos/${id}/consultar-detran`,
      {
        method: "POST",
        body: JSON.stringify({ uf }),
      },
    );
  } catch {
    // a row de erro ja foi gravada no DB; nao crashar a Server Action
  }
  revalidatePath(`/manutencao/veiculos/${id}`);
}

export default async function VeiculoDetalhe({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: idStr } = await params;
  const id = Number(idStr);
  if (!Number.isFinite(id)) notFound();

  const [veiculo, consultas] = await Promise.all([
    fetchVeiculo(id),
    fetchConsultas(id),
  ]);
  if (!veiculo) notFound();

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">
            {formatPlaca(veiculo.placa)}{" "}
            <span
              className={`ml-3 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                STATUS_BADGE[veiculo.status] ??
                "bg-slate-100 text-slate-700"
              }`}
            >
              {veiculo.status}
            </span>
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            {veiculo.marca ?? "—"}{" "}
            {veiculo.modelo ? <span>{veiculo.modelo}</span> : null}{" "}
            {veiculo.ano_modelo ? <span>({veiculo.ano_modelo})</span> : null}
            {veiculo.obra ? (
              <span className="ml-2 text-slate-400">· {veiculo.obra}</span>
            ) : null}
          </p>
        </div>
        <Link
          href="/manutencao/veiculos"
          className="text-xs text-slate-500 hover:text-slate-800"
        >
          ← voltar para frota
        </Link>
      </header>

      {/* Ficha tecnica */}
      <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <h2 className="mb-3 text-sm font-semibold">Ficha</h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 md:grid-cols-4">
          <Field label="Renavam" value={veiculo.renavam ?? "—"} />
          <Field label="Chassi" value={veiculo.chassi ?? "—"} mono />
          <Field label="Cor" value={veiculo.cor ?? "—"} />
          <Field label="Tipo" value={veiculo.tipo ?? "—"} />
          <Field
            label="Km atual"
            value={
              veiculo.km_atual !== null
                ? veiculo.km_atual.toLocaleString("pt-BR")
                : "—"
            }
          />
        </dl>
      </section>

      {/* Acao -- consultar Detran */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Consulta Detran</h2>
        <p className="mt-1 text-xs text-slate-500">
          Via Infosimples (sem credencial de despachante). Mock determinístico
          em modo dev — ligando <code>INFOSIMPLES_TOKEN</code> no env, o
          adapter passa para o modo real automaticamente.
        </p>
        <form
          action={consultarDetran}
          className="mt-3 flex flex-wrap items-center gap-2"
        >
          <input type="hidden" name="id" value={veiculo.id} />
          <label className="flex items-center gap-2 text-xs">
            <span className="font-medium uppercase text-slate-500">UF</span>
            <select
              name="uf"
              defaultValue="SP"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="SP">SP</option>
              <option value="MG">MG</option>
              <option value="GO">GO</option>
            </select>
          </label>
          <button
            type="submit"
            className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
          >
            Consultar Detran
          </button>
        </form>
      </section>

      {/* Historico de consultas */}
      <section className="rounded-lg border border-slate-200 bg-white">
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold">
            Histórico de consultas ({consultas.total})
          </h2>
        </header>
        {consultas.items.length === 0 ? (
          <p className="px-4 py-8 text-sm text-slate-500">
            Nenhuma consulta ainda. Use o botão acima para a primeira.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {consultas.items.map((c) => (
              <li key={c.id} className="px-4 py-4 text-sm">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        CONSULTA_BADGE[c.status] ??
                        "bg-slate-100 text-slate-700"
                      }`}
                    >
                      {c.status}
                    </span>
                    <span className="text-xs text-slate-400">{c.source}</span>
                    <span className="text-xs text-slate-500">{c.uf}</span>
                  </div>
                  <span className="text-xs text-slate-400">
                    {fmtDateTime(c.executed_at)}
                  </span>
                </div>

                {c.error_msg ? (
                  <p className="mt-2 rounded bg-red-50 px-3 py-2 text-xs text-red-700">
                    {c.error_msg}
                  </p>
                ) : null}

                {c.payload ? (
                  <ConsultaCorpo payload={c.payload} />
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Documentos do veiculo */}
      <section className="rounded-lg border border-slate-200 bg-white">
        <header className="border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold">
            Documentos ({veiculo.documentos.length})
          </h2>
        </header>
        {veiculo.documentos.length === 0 ? (
          <p className="px-4 py-8 text-sm text-slate-500">
            Nenhum documento cadastrado. Consultas Detran materializam IPVA e
            licenciamento automaticamente.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2 text-left">Tipo</th>
                <th className="px-4 py-2 text-left">Número</th>
                <th className="px-4 py-2 text-left">Validade</th>
                <th className="px-4 py-2 text-left">Origem</th>
                <th className="px-4 py-2 text-left">Obs</th>
              </tr>
            </thead>
            <tbody>
              {veiculo.documentos.map((d) => (
                <tr
                  key={d.id}
                  className="border-t border-slate-100 text-slate-700"
                >
                  <td className="px-4 py-2 font-medium">{d.tipo}</td>
                  <td className="px-4 py-2 text-xs">{d.numero ?? "—"}</td>
                  <td className="px-4 py-2 text-xs">{fmtDate(d.validade)}</td>
                  <td className="px-4 py-2 text-xs text-slate-500">
                    {d.source}
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-500">
                    {d.observacoes ?? "—"}
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

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">
        {label}
      </dt>
      <dd className={`text-sm text-slate-700 ${mono ? "font-mono" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

function ConsultaCorpo({ payload }: { payload: ConsultaPayload }) {
  const ipva = payload.ipva;
  const lic = payload.licenciamento;
  const multas = payload.multas ?? [];
  const restricoes = payload.restricoes ?? [];
  return (
    <div className="mt-3 grid grid-cols-1 gap-3 text-xs md:grid-cols-3">
      <div className="rounded border border-slate-200 p-2">
        <div className="text-xs uppercase text-slate-400">Situação</div>
        <span
          className={`mt-1 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
            (payload.situacao && SITUACAO_BADGE[payload.situacao]) ??
            "bg-slate-100 text-slate-700"
          }`}
        >
          {payload.situacao ?? "—"}
        </span>
        {restricoes.length > 0 ? (
          <ul className="mt-2 list-inside list-disc text-xs text-slate-600">
            {restricoes.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="rounded border border-slate-200 p-2">
        <div className="text-xs uppercase text-slate-400">IPVA</div>
        {ipva ? (
          <>
            <div>
              {ipva.exercicio ?? "—"}{" "}
              <span className="text-slate-400">
                · venc {fmtDate(ipva.vencimento)}
              </span>
            </div>
            <div className="text-slate-600">
              {ipva.pago ? "pago" : "em aberto"}
              {ipva.valor ? ` · R$ ${ipva.valor}` : null}
            </div>
          </>
        ) : (
          <span className="text-slate-400">sem dados</span>
        )}
      </div>
      <div className="rounded border border-slate-200 p-2">
        <div className="text-xs uppercase text-slate-400">Licenciamento</div>
        {lic ? (
          <>
            <div>
              {lic.exercicio ?? "—"}{" "}
              <span className="text-slate-400">
                · venc {fmtDate(lic.vencimento)}
              </span>
            </div>
            <div className="text-slate-600">
              {lic.pago ? "pago" : "em aberto"}
              {lic.valor ? ` · R$ ${lic.valor}` : null}
            </div>
          </>
        ) : (
          <span className="text-slate-400">sem dados</span>
        )}
      </div>
      {multas.length > 0 ? (
        <div className="md:col-span-3 rounded border border-slate-200 p-2">
          <div className="text-xs uppercase text-slate-400">
            Multas ({multas.length})
          </div>
          <table className="mt-2 w-full text-xs">
            <thead className="text-slate-500">
              <tr>
                <th className="text-left">Auto</th>
                <th className="text-left">Data</th>
                <th className="text-left">Valor</th>
                <th className="text-left">Descrição</th>
              </tr>
            </thead>
            <tbody>
              {multas.map((m, i) => (
                <tr key={i} className="border-t border-slate-100">
                  <td className="font-mono">{m.auto ?? "—"}</td>
                  <td>{fmtDate(m.data)}</td>
                  <td>{m.valor ? `R$ ${m.valor}` : "—"}</td>
                  <td>{m.descricao ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
