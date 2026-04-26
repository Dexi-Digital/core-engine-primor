import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type ParteDiaria = {
  id: number;
  filename_original: string | null;
  data: string | null;
  veiculo_id: number | null;
  operador: string | null;
  obra: string | null;
  equipamento: string | null;
  placa: string | null;
  horimetro_inicio: string | null;
  horimetro_fim: string | null;
  km_inicio: number | null;
  km_fim: number | null;
  ocr_status: string;
  ocr_source: string;
  ocr_confidence: string | null;
  ocr_error_msg: string | null;
  created_at: string;
};

type ListResponse = {
  items: ParteDiaria[];
  total: number;
  page: number;
  page_size: number;
};

type ParteDiariaConsumo = {
  parte_diaria_id: number;
  horas_trabalhadas: string | null;
  km_rodados: number | null;
  alerta_manutencao_preventiva: boolean;
};

const OCR_STATUSES: Array<[string, string]> = [
  ["pendente", "Pendente"],
  ["processado", "Processado"],
  ["revisado", "Revisado"],
  ["erro", "Erro"],
];

const STATUS_BADGE: Record<string, string> = {
  pendente: "bg-slate-100 text-slate-700",
  processado: "bg-amber-100 text-amber-700",
  revisado: "bg-emerald-100 text-emerald-700",
  erro: "bg-rose-100 text-rose-700",
};

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const dynamic = "force-dynamic";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("pt-BR");
  } catch {
    return iso;
  }
}

async function fetchPartes(params: {
  ocr_status?: string;
  obra?: string;
  placa?: string;
}): Promise<ListResponse | null> {
  const qs = new URLSearchParams();
  if (params.ocr_status) qs.set("ocr_status", params.ocr_status);
  if (params.obra) qs.set("obra", params.obra);
  if (params.placa) qs.set("placa", params.placa);
  const path = `/api/v1/manutencao-frota/partes-diarias${
    qs.toString() ? `?${qs}` : ""
  }`;
  try {
    return await apiFetch<ListResponse>(path);
  } catch {
    return null;
  }
}

async function fetchConsumo(
  id: number,
): Promise<ParteDiariaConsumo | null> {
  try {
    return await apiFetch<ParteDiariaConsumo>(
      `/api/v1/manutencao-frota/partes-diarias/${id}/consumo`,
    );
  } catch {
    return null;
  }
}

/**
 * Fetch consumo em paralelo para um conjunto de partes. Indexa por id
 * para a UI poder lookup O(1) ao renderizar a linha.
 *
 * E O(n) chamadas mas como o backend e SQL leve (1 query por consumo)
 * e o page_size default e 50, fica aceitavel. Se virar gargalo, vira
 * endpoint batch (POST /partes-diarias/consumos com body=[ids]).
 */
async function fetchConsumosBatch(
  ids: number[],
): Promise<Map<number, ParteDiariaConsumo>> {
  const results = await Promise.all(ids.map((id) => fetchConsumo(id)));
  const map = new Map<number, ParteDiariaConsumo>();
  results.forEach((c) => {
    if (c !== null) map.set(c.parte_diaria_id, c);
  });
  return map;
}

async function uploadParteDiaria(formData: FormData): Promise<void> {
  "use server";
  const arquivo = formData.get("arquivo") as File | null;
  if (!arquivo || arquivo.size === 0) return;
  const upload = new FormData();
  upload.append("arquivo", arquivo);
  const obra = String(formData.get("obra") ?? "").trim();
  const veiculoId = String(formData.get("veiculo_id") ?? "").trim();
  if (obra) upload.append("obra", obra);
  if (veiculoId) upload.append("veiculo_id", veiculoId);
  // Multipart -- nao da pra usar `apiFetch` (forca content-type JSON).
  await fetch(`${API_BASE}/api/v1/manutencao-frota/partes-diarias`, {
    method: "POST",
    body: upload,
  });
  revalidatePath("/manutencao/partes-diarias");
}

async function deleteParteDiaria(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  await apiFetch(`/api/v1/manutencao-frota/partes-diarias/${id}`, {
    method: "DELETE",
  });
  revalidatePath("/manutencao/partes-diarias");
}

export default async function PartesDiariasPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const ocrStatus = (params.ocr_status as string | undefined) || "";
  const obra = (params.obra as string | undefined) || "";
  const placa = (params.placa as string | undefined) || "";
  const list = await fetchPartes({
    ocr_status: ocrStatus || undefined,
    obra: obra || undefined,
    placa: placa || undefined,
  });
  const consumos = list
    ? await fetchConsumosBatch(list.items.map((p) => p.id))
    : new Map<number, ParteDiariaConsumo>();
  const apenasComAlerta = (params.alerta as string | undefined) === "1";
  const itensVisiveis = list
    ? apenasComAlerta
      ? list.items.filter(
          (p) => consumos.get(p.id)?.alerta_manutencao_preventiva,
        )
      : list.items
    : [];
  const totalAlertas = list
    ? list.items.filter(
        (p) => consumos.get(p.id)?.alerta_manutencao_preventiva,
      ).length
    : 0;

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-xl font-semibold">Partes diárias (OCR)</h1>
        <p className="text-sm text-slate-500">
          Upload de PDF/foto da parte diária → OCR via Google Document AI →
          extração de operador, obra, equipamento, horímetros e KM.
        </p>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-base font-semibold">Nova parte diária</h2>
        <p className="mt-1 text-xs text-slate-500">
          Aceita PDF ou imagem (JPG/PNG). Se a placa for reconhecida e o
          veículo já estiver cadastrado, ele é amarrado automaticamente.
        </p>
        <form
          action={uploadParteDiaria}
          encType="multipart/form-data"
          className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3"
        >
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700 sm:col-span-3">
            Arquivo
            <input
              name="arquivo"
              type="file"
              accept=".pdf,image/png,image/jpeg"
              required
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Obra (opcional)
            <input
              name="obra"
              type="text"
              maxLength={200}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            ID do veículo (opcional)
            <input
              name="veiculo_id"
              type="number"
              min={1}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <button
            type="submit"
            className="self-end rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            Enviar e processar OCR
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-base font-semibold">Filtros</h2>
        <form
          method="get"
          className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-4"
        >
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Status OCR
            <select
              name="ocr_status"
              defaultValue={ocrStatus}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">— todos —</option>
              {OCR_STATUSES.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Obra
            <input
              name="obra"
              type="text"
              defaultValue={obra}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Placa
            <input
              name="placa"
              type="text"
              defaultValue={placa}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <div className="flex items-end gap-2">
            <button
              type="submit"
              className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
            >
              Filtrar
            </button>
            <Link
              href="/manutencao/partes-diarias"
              className="text-xs text-slate-500 underline"
            >
              limpar
            </Link>
          </div>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-6 py-4">
          <h2 className="text-base font-semibold">
            Lançamentos {list ? `(${list.total})` : ""}
          </h2>
          {list && totalAlertas > 0 && (
            <Link
              href={
                apenasComAlerta
                  ? `?${new URLSearchParams({
                      ...(ocrStatus ? { ocr_status: ocrStatus } : {}),
                      ...(obra ? { obra } : {}),
                      ...(placa ? { placa } : {}),
                    }).toString()}`
                  : `?${new URLSearchParams({
                      ...(ocrStatus ? { ocr_status: ocrStatus } : {}),
                      ...(obra ? { obra } : {}),
                      ...(placa ? { placa } : {}),
                      alerta: "1",
                    }).toString()}`
              }
              className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium ${
                apenasComAlerta
                  ? "border-amber-500 bg-amber-100 text-amber-900"
                  : "border-amber-300 bg-amber-50 text-amber-800 hover:border-amber-500"
              }`}
            >
              {apenasComAlerta
                ? `mostrando ${totalAlertas} com alerta · limpar filtro`
                : `${totalAlertas} com alerta de manutenção · filtrar`}
            </Link>
          )}
        </header>
        {list === null ? (
          <p className="px-6 py-8 text-sm text-slate-500">
            API indisponível.
          </p>
        ) : itensVisiveis.length === 0 ? (
          <p className="px-6 py-8 text-sm text-slate-500">
            {apenasComAlerta
              ? "Nenhuma parte com alerta de manutenção nesse filtro."
              : "Nenhuma parte diária com esses filtros ainda."}
          </p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-6 py-3">Data</th>
                <th className="px-6 py-3">Operador</th>
                <th className="px-6 py-3">Obra</th>
                <th className="px-6 py-3">Equipamento</th>
                <th className="px-6 py-3">Placa</th>
                <th className="px-6 py-3">Status OCR</th>
                <th className="px-6 py-3">Manutenção</th>
                <th className="px-6 py-3">Arquivo</th>
                <th className="px-6 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {itensVisiveis.map((p) => {
                const c = consumos.get(p.id);
                return (
                <tr key={p.id} className="border-t border-slate-100">
                  <td className="px-6 py-3">{formatDate(p.data)}</td>
                  <td className="px-6 py-3">{p.operador ?? "—"}</td>
                  <td className="px-6 py-3">{p.obra ?? "—"}</td>
                  <td className="px-6 py-3">{p.equipamento ?? "—"}</td>
                  <td className="px-6 py-3 font-mono">{p.placa ?? "—"}</td>
                  <td className="px-6 py-3">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                        STATUS_BADGE[p.ocr_status] ??
                        "bg-slate-100 text-slate-700"
                      }`}
                    >
                      {p.ocr_status}
                    </span>
                  </td>
                  <td className="px-6 py-3">
                    {c?.alerta_manutencao_preventiva ? (
                      <span
                        className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
                        title="Atravessou marco de 250 horas desde o último apontamento"
                      >
                        ⚠ 250h
                      </span>
                    ) : c?.horas_trabalhadas ? (
                      <span className="text-xs text-slate-500">
                        {c.horas_trabalhadas} h
                      </span>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </td>
                  <td className="px-6 py-3 text-xs text-slate-500">
                    {p.filename_original ?? "—"}
                  </td>
                  <td className="px-6 py-3 text-right">
                    <Link
                      href={`/manutencao/partes-diarias/${p.id}`}
                      className="text-xs text-slate-700 underline hover:text-slate-900"
                    >
                      detalhes
                    </Link>
                    <form action={deleteParteDiaria} className="inline">
                      <input type="hidden" name="id" value={p.id} />
                      <button
                        type="submit"
                        className="ml-3 text-xs text-rose-600 underline hover:text-rose-800"
                      >
                        excluir
                      </button>
                    </form>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
