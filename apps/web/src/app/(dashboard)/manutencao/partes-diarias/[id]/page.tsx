import Link from "next/link";
import { revalidatePath } from "next/cache";
import { notFound } from "next/navigation";

import { apiFetch } from "@/lib/api";

type ParteDiaria = {
  id: number;
  filename_original: string | null;
  mime_type: string | null;
  anexo_path: string | null;
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
  combustivel_litros: string | null;
  combustivel_custo: string | null;
  observacoes: string | null;
  ocr_status: string;
  ocr_source: string;
  ocr_confidence: string | null;
  ocr_payload: Record<string, unknown> | null;
  ocr_error_msg: string | null;
  created_at: string;
  updated_at: string;
};

type ParteDiariaConsumo = {
  parte_diaria_id: number;
  horas_trabalhadas: string | null;
  km_rodados: number | null;
  consumo_litros_por_hora: string | null;
  consumo_km_por_litro: string | null;
  custo_por_hora: string | null;
  alerta_manutencao_preventiva: boolean;
};

const STATUS_BADGE: Record<string, string> = {
  pendente: "bg-slate-100 text-slate-700",
  processado: "bg-amber-100 text-amber-700",
  revisado: "bg-emerald-100 text-emerald-700",
  erro: "bg-rose-100 text-rose-700",
};

export const dynamic = "force-dynamic";

async function fetchParte(id: string): Promise<ParteDiaria | null> {
  try {
    return await apiFetch<ParteDiaria>(
      `/api/v1/manutencao-frota/partes-diarias/${id}`,
    );
  } catch {
    return null;
  }
}

async function fetchConsumo(
  id: string | number,
): Promise<ParteDiariaConsumo | null> {
  try {
    return await apiFetch<ParteDiariaConsumo>(
      `/api/v1/manutencao-frota/partes-diarias/${id}/consumo`,
    );
  } catch {
    return null;
  }
}

async function reprocessar(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  try {
    await apiFetch(
      `/api/v1/manutencao-frota/partes-diarias/${id}/reprocessar`,
      { method: "POST" },
    );
  } catch {
    // ja persistido como erro do lado do backend.
  }
  revalidatePath(`/manutencao/partes-diarias/${id}`);
  revalidatePath("/manutencao/partes-diarias");
}

async function salvarRevisao(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;

  const numericOrNull = (v: FormDataEntryValue | null): number | null => {
    const s = String(v ?? "").trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  };

  const payload: Record<string, unknown> = {
    data: String(formData.get("data") ?? "").trim() || null,
    operador: String(formData.get("operador") ?? "").trim() || null,
    obra: String(formData.get("obra") ?? "").trim() || null,
    equipamento: String(formData.get("equipamento") ?? "").trim() || null,
    placa:
      String(formData.get("placa") ?? "")
        .trim()
        .toUpperCase() || null,
    horimetro_inicio: numericOrNull(formData.get("horimetro_inicio")),
    horimetro_fim: numericOrNull(formData.get("horimetro_fim")),
    km_inicio: numericOrNull(formData.get("km_inicio")),
    km_fim: numericOrNull(formData.get("km_fim")),
    combustivel_litros: numericOrNull(formData.get("combustivel_litros")),
    combustivel_custo: numericOrNull(formData.get("combustivel_custo")),
    observacoes: String(formData.get("observacoes") ?? "").trim() || null,
  };
  await apiFetch(`/api/v1/manutencao-frota/partes-diarias/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  revalidatePath(`/manutencao/partes-diarias/${id}`);
  revalidatePath("/manutencao/partes-diarias");
}

export default async function ParteDiariaDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const parte = await fetchParte(id);
  if (!parte) notFound();
  // Consumo e alerta dependem de campos que podem nao existir
  // ainda (ex.: ocr pendente sem horimetro) -- silenciosamente
  // null se a parte ainda nao tem dados pra calcular.
  const consumo = await fetchConsumo(parte.id);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center justify-between">
        <div>
          <Link
            href="/manutencao/partes-diarias"
            className="text-xs text-slate-500 underline"
          >
            ← Voltar para partes diárias
          </Link>
          <h1 className="mt-1 text-xl font-semibold">
            Parte diária #{parte.id}
          </h1>
          <p className="text-sm text-slate-500">
            {parte.filename_original ?? "(sem arquivo)"} · OCR{" "}
            <span
              className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                STATUS_BADGE[parte.ocr_status] ?? "bg-slate-100 text-slate-700"
              }`}
            >
              {parte.ocr_status}
            </span>{" "}
            · fonte: {parte.ocr_source}
          </p>
        </div>
        <form action={reprocessar}>
          <input type="hidden" name="id" value={parte.id} />
          <button
            type="submit"
            className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:border-slate-500"
          >
            Reprocessar OCR
          </button>
        </form>
      </header>

      {parte.ocr_status === "pendente" && (
        <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
          <strong>Processando OCR…</strong> a tarefa foi enfileirada no
          worker. Atualize esta página em alguns segundos para ver os
          campos extraídos.{" "}
          <a
            href={`/manutencao/partes-diarias/${parte.id}`}
            className="underline"
          >
            Atualizar agora
          </a>
        </div>
      )}

      {parte.ocr_error_msg && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <strong>Erro OCR:</strong> {parte.ocr_error_msg}
        </div>
      )}

      {consumo?.alerta_manutencao_preventiva && (
        <div className="rounded-md border-2 border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <strong>Alerta de manutenção preventiva.</strong> Este
          equipamento atravessou um múltiplo de 250 horas de uso desde
          o último apontamento — agendar revisão (troca de óleo motor,
          filtros, etc.) conforme manual do fabricante.
        </div>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-base font-semibold">Consumo</h2>
        <p className="mt-1 text-xs text-slate-500">
          Calculado a partir dos campos abaixo. Métricas vazias = falta
          de dado (horímetro / km / combustível) na parte ou na
          predecessora do mesmo veículo.
        </p>
        {consumo === null ? (
          <p className="mt-3 text-sm text-slate-500">
            Sem dados suficientes para calcular consumo ainda.
          </p>
        ) : (
          <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
            <ConsumoMetric
              label="Horas trabalhadas"
              value={
                consumo.horas_trabalhadas
                  ? `${consumo.horas_trabalhadas} h`
                  : null
              }
            />
            <ConsumoMetric
              label="KM rodados"
              value={
                consumo.km_rodados !== null ? `${consumo.km_rodados} km` : null
              }
            />
            <ConsumoMetric
              label="Consumo (L/h)"
              value={consumo.consumo_litros_por_hora}
            />
            <ConsumoMetric
              label="Consumo (km/L)"
              value={consumo.consumo_km_por_litro}
            />
            <ConsumoMetric
              label="Custo por hora"
              value={
                consumo.custo_por_hora ? `R$ ${consumo.custo_por_hora}` : null
              }
            />
            <ConsumoMetric
              label="Manutenção 250h"
              value={
                consumo.alerta_manutencao_preventiva
                  ? "Marco atravessado"
                  : "Sem gatilho"
              }
              tone={
                consumo.alerta_manutencao_preventiva ? "alert" : "default"
              }
            />
          </dl>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-base font-semibold">Campos extraídos</h2>
        <p className="mt-1 text-xs text-slate-500">
          Confira e ajuste se necessário. Salvar move o status para
          “revisado”.
        </p>
        <form
          action={salvarRevisao}
          className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2"
        >
          <input type="hidden" name="id" value={parte.id} />
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Data
            <input
              name="data"
              type="date"
              defaultValue={parte.data ?? ""}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Placa
            <input
              name="placa"
              type="text"
              defaultValue={parte.placa ?? ""}
              maxLength={8}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm font-mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Operador
            <input
              name="operador"
              type="text"
              defaultValue={parte.operador ?? ""}
              maxLength={200}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Obra
            <input
              name="obra"
              type="text"
              defaultValue={parte.obra ?? ""}
              maxLength={200}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700 sm:col-span-2">
            Equipamento
            <input
              name="equipamento"
              type="text"
              defaultValue={parte.equipamento ?? ""}
              maxLength={200}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Horímetro inicial
            <input
              name="horimetro_inicio"
              type="number"
              step="0.01"
              defaultValue={parte.horimetro_inicio ?? ""}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Horímetro final
            <input
              name="horimetro_fim"
              type="number"
              step="0.01"
              defaultValue={parte.horimetro_fim ?? ""}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            KM inicial
            <input
              name="km_inicio"
              type="number"
              defaultValue={parte.km_inicio ?? ""}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            KM final
            <input
              name="km_fim"
              type="number"
              defaultValue={parte.km_fim ?? ""}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Combustível (litros)
            <input
              name="combustivel_litros"
              type="number"
              step="0.001"
              min={0}
              defaultValue={parte.combustivel_litros ?? ""}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
            Custo combustível (R$)
            <input
              name="combustivel_custo"
              type="number"
              step="0.01"
              min={0}
              defaultValue={parte.combustivel_custo ?? ""}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-slate-700 sm:col-span-2">
            Observações
            <textarea
              name="observacoes"
              defaultValue={parte.observacoes ?? ""}
              rows={3}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </label>
          <button
            type="submit"
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 sm:col-span-2"
          >
            Salvar revisão
          </button>
        </form>
      </section>

      {parte.ocr_payload && (
        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-base font-semibold">Resposta bruta do OCR</h2>
          <pre className="mt-3 max-h-96 overflow-auto rounded-md bg-slate-50 p-3 text-xs text-slate-700">
            {JSON.stringify(parte.ocr_payload, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
}

function ConsumoMetric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string | null;
  tone?: "default" | "alert";
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd
        className={`mt-1 text-base font-semibold ${
          tone === "alert" ? "text-amber-700" : "text-slate-900"
        }`}
      >
        {value ?? "—"}
      </dd>
    </div>
  );
}
