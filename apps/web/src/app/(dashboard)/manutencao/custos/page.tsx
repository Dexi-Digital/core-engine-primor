/**
 * Custo e apropriação por equipamento (roadmap B#7).
 *
 * Fora-da-curva no topo, não o total: quem abre a tela quer saber onde
 * o dinheiro está vazando, não conferir a soma. A tabela completa vem
 * embaixo, ordenada por custo.
 */
import Link from "next/link";

import { apiFetch } from "@/lib/api";

type Equipamento = {
  veiculo_id: number;
  placa: string;
  tipo: string | null;
  modelo: string | null;
  obra: string | null;
  apontamentos: number;
  horas_trabalhadas: string;
  km_rodados: number;
  litros: string;
  custo_total: string;
  consumo_litros_por_hora: string | null;
  consumo_km_por_litro: string | null;
  custo_por_hora: string | null;
  custo_por_km: string | null;
  referencia_litros_por_hora: string | null;
  desvio_percentual: string | null;
  alerta_custo: boolean;
};

type Apropriacao = {
  periodo: { inicio: string | null; fim: string | null };
  obra: string | null;
  equipamentos: Equipamento[];
  totais: {
    equipamentos: number;
    apontamentos: number;
    horas: string;
    km: number;
    litros: string;
    custo_total: string;
  };
  fora_da_curva: Equipamento[];
};

export const dynamic = "force-dynamic";

function brl(v: string | null): string {
  if (v === null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function num(v: string | number | null, casas = 2): string {
  if (v === null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("pt-BR", {
    minimumFractionDigits: casas,
    maximumFractionDigits: casas,
  });
}

async function fetchApropriacao(params: {
  inicio?: string;
  fim?: string;
  obra?: string;
}): Promise<Apropriacao | null> {
  const qs = new URLSearchParams();
  if (params.inicio) qs.set("inicio", params.inicio);
  if (params.fim) qs.set("fim", params.fim);
  if (params.obra) qs.set("obra", params.obra);
  const path = `/api/v1/manutencao-frota/custo-equipamento${qs.toString() ? `?${qs}` : ""}`;
  try {
    return await apiFetch<Apropriacao>(path);
  } catch {
    return null;
  }
}

export default async function CustosPage({
  searchParams,
}: {
  searchParams: Promise<{ inicio?: string; fim?: string; obra?: string }>;
}) {
  const params = await searchParams;
  const dados = await fetchApropriacao(params);

  if (!dados) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-800">
        Não foi possível carregar a apropriação. Verifique se a API está no ar.
      </div>
    );
  }

  const vazio = dados.equipamentos.length === 0;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Custo por equipamento</h1>
          <p className="mt-1 text-sm text-slate-600">
            Consumo e custo apurados a partir das partes diárias. A referência
            é a mediana dos equipamentos do mesmo tipo na própria frota — não
            um número de catálogo.
          </p>
        </div>
        <Link
          href="/manutencao"
          className="text-xs text-slate-500 hover:text-slate-800"
        >
          ← voltar para Manutenção
        </Link>
      </header>

      <form
        method="get"
        className="grid grid-cols-1 gap-3 rounded-lg border border-slate-200 bg-white p-4 md:grid-cols-4"
      >
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            De
          </span>
          <input
            type="date"
            name="inicio"
            defaultValue={params.inicio ?? ""}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium uppercase tracking-wide text-slate-500">
            Até
          </span>
          <input
            type="date"
            name="fim"
            defaultValue={params.fim ?? ""}
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
            placeholder="todas"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <div className="flex items-end">
          <button
            type="submit"
            className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
          >
            Filtrar
          </button>
        </div>
      </form>

      {vazio ? (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-600">
          Nenhuma parte diária revisada no período. O custo é apurado a partir
          dos apontamentos — assim que houver partes diárias processadas, os
          números aparecem aqui.
        </div>
      ) : (
        <>
          <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {[
              ["Equipamentos", String(dados.totais.equipamentos)],
              ["Apontamentos", String(dados.totais.apontamentos)],
              ["Horas", num(dados.totais.horas, 1)],
              ["Litros", num(dados.totais.litros, 1)],
              ["Custo total", brl(dados.totais.custo_total)],
            ].map(([rotulo, valor]) => (
              <div
                key={rotulo}
                className="rounded-lg border border-slate-200 bg-white p-3"
              >
                <div className="text-xl font-semibold">{valor}</div>
                <div className="mt-1 text-xs text-slate-500">{rotulo}</div>
              </div>
            ))}
          </section>

          {dados.fora_da_curva.length > 0 && (
            <section className="rounded-lg border border-red-200 bg-red-50 p-4">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-red-800">
                Fora da curva — {dados.fora_da_curva.length} equipamento(s)
              </h2>
              <p className="mt-1 text-xs text-red-700">
                Consumo mais de 30% acima da mediana dos equipamentos do mesmo
                tipo no período.
              </p>
              <ul className="mt-3 space-y-1 text-sm text-red-900">
                {dados.fora_da_curva.map((e) => (
                  <li key={e.veiculo_id}>
                    <span className="font-medium">{e.placa}</span>{" "}
                    {e.modelo ? `(${e.modelo})` : ""} — {num(e.consumo_litros_por_hora, 2)} L/h
                    contra referência de {num(e.referencia_litros_por_hora, 2)} L/h
                    {e.desvio_percentual
                      ? ` · +${num(e.desvio_percentual, 1)}%`
                      : ""}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2">Placa</th>
                  <th className="px-3 py-2">Tipo</th>
                  <th className="px-3 py-2">Obra</th>
                  <th className="px-3 py-2 text-right">Apont.</th>
                  <th className="px-3 py-2 text-right">Horas</th>
                  <th className="px-3 py-2 text-right">Km</th>
                  <th className="px-3 py-2 text-right">Litros</th>
                  <th className="px-3 py-2 text-right">L/h</th>
                  <th className="px-3 py-2 text-right">km/L</th>
                  <th className="px-3 py-2 text-right">R$/h</th>
                  <th className="px-3 py-2 text-right">R$/km</th>
                  <th className="px-3 py-2 text-right">Custo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {dados.equipamentos.map((e) => (
                  <tr
                    key={e.veiculo_id}
                    className={e.alerta_custo ? "bg-red-50" : undefined}
                  >
                    <td className="px-3 py-2 font-medium">
                      <Link
                        href={`/manutencao/veiculos/${e.veiculo_id}`}
                        className="hover:underline"
                      >
                        {e.placa}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-slate-600">{e.tipo ?? "—"}</td>
                    <td className="px-3 py-2 text-slate-600">{e.obra ?? "—"}</td>
                    <td className="px-3 py-2 text-right">{e.apontamentos}</td>
                    <td className="px-3 py-2 text-right">{num(e.horas_trabalhadas, 1)}</td>
                    <td className="px-3 py-2 text-right">{e.km_rodados || "—"}</td>
                    <td className="px-3 py-2 text-right">{num(e.litros, 1)}</td>
                    <td className="px-3 py-2 text-right">{num(e.consumo_litros_por_hora, 2)}</td>
                    <td className="px-3 py-2 text-right">{num(e.consumo_km_por_litro, 2)}</td>
                    <td className="px-3 py-2 text-right">{brl(e.custo_por_hora)}</td>
                    <td className="px-3 py-2 text-right">{brl(e.custo_por_km)}</td>
                    <td className="px-3 py-2 text-right font-medium">{brl(e.custo_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
