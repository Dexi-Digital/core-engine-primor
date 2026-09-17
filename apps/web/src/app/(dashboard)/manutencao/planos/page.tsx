/**
 * Planos de manutenção — revisões programadas por equipamento.
 *
 * Vencidas no topo, e "sem plano cadastrado" logo depois: numa tela de
 * manutenção, a ausência de alerta é lida como "tudo em dia", então o
 * equipamento sem plano precisa aparecer com o mesmo peso de um
 * vencimento.
 */
import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch } from "@/lib/api";

type Plano = {
  plano_id: number;
  veiculo_id: number;
  placa: string;
  descricao: string;
  base: string;
  intervalo: string;
  leitura_atual: string | null;
  ultima_revisao_marcador: string | null;
  proxima_em: string | null;
  falta: string | null;
  status: string;
};

type SemPlano = {
  veiculo_id: number;
  placa: string;
  tipo: string | null;
  modelo: string | null;
  obra: string | null;
};

type Painel = {
  total: number;
  por_status: Record<string, number>;
  vencidas: Plano[];
  planos: Plano[];
  sem_plano: SemPlano[];
};

type Veiculo = { id: number; placa: string; modelo: string | null };

const STATUS_BADGE: Record<string, { rotulo: string; classe: string }> = {
  vencida: { rotulo: "Vencida", classe: "bg-red-100 text-red-700" },
  proxima: { rotulo: "Próxima", classe: "bg-amber-100 text-amber-700" },
  ok: { rotulo: "Em dia", classe: "bg-emerald-100 text-emerald-700" },
  sem_leitura: {
    rotulo: "Sem leitura",
    classe: "bg-slate-200 text-slate-700",
  },
};

const UNIDADE: Record<string, string> = { km: "km", horas: "h" };

export const dynamic = "force-dynamic";

function num(v: string | null): string {
  if (v === null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
}

async function fetchPainel(): Promise<Painel | null> {
  try {
    return await apiFetch<Painel>("/api/v1/manutencao-frota/planos-manutencao");
  } catch {
    return null;
  }
}

async function fetchVeiculos(): Promise<Veiculo[]> {
  try {
    const r = await apiFetch<{ items: Veiculo[] }>(
      "/api/v1/manutencao-frota/veiculos?page=1&page_size=200&status=ativo",
    );
    return r.items ?? [];
  } catch {
    return [];
  }
}

async function criarPlano(formData: FormData): Promise<void> {
  "use server";
  const veiculo_id = Number(formData.get("veiculo_id"));
  const descricao = String(formData.get("descricao") ?? "").trim();
  const base = String(formData.get("base") ?? "horas");
  const intervalo = String(formData.get("intervalo") ?? "").trim();
  if (!veiculo_id || !descricao || !intervalo) return;
  const marcador = String(formData.get("ultima_revisao_marcador") ?? "").trim();
  await apiFetch("/api/v1/manutencao-frota/planos-manutencao", {
    method: "POST",
    body: JSON.stringify({
      veiculo_id,
      descricao,
      base,
      intervalo,
      ultima_revisao_marcador: marcador || null,
    }),
  });
  revalidatePath("/manutencao/planos");
}

async function registrarRevisao(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("plano_id");
  if (!id) return;
  const marcador = String(formData.get("marcador") ?? "").trim();
  await apiFetch(
    `/api/v1/manutencao-frota/planos-manutencao/${id}/revisao`,
    {
      method: "POST",
      body: JSON.stringify({
        marcador: marcador || null,
        data: new Date().toISOString().slice(0, 10),
      }),
    },
  );
  revalidatePath("/manutencao/planos");
}

export default async function PlanosPage() {
  const [painel, veiculos] = await Promise.all([
    fetchPainel(),
    fetchVeiculos(),
  ]);

  if (!painel) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-800">
        Não foi possível carregar os planos. Verifique se a API está no ar.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Planos de manutenção</h1>
          <p className="mt-1 text-sm text-slate-600">
            Revisões programadas por equipamento. O vencimento é medido em{" "}
            <strong>uso</strong> (horímetro/odômetro), não em calendário —
            máquina parada não precisa de troca de óleo por causa do tempo.
          </p>
        </div>
        <Link
          href="/manutencao"
          className="text-xs text-slate-500 hover:text-slate-800"
        >
          ← voltar para Manutenção
        </Link>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {(["vencida", "proxima", "ok", "sem_leitura"] as const).map((s) => (
          <div key={s} className="rounded-lg border border-slate-200 bg-white p-3">
            <div className="text-2xl font-semibold">
              {painel.por_status[s] ?? 0}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {STATUS_BADGE[s].rotulo}
            </div>
          </div>
        ))}
      </section>

      {painel.sem_plano.length > 0 && (
        <section className="rounded-lg border border-orange-200 bg-orange-50 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-orange-800">
            Sem plano cadastrado — {painel.sem_plano.length} equipamento(s)
          </h2>
          <p className="mt-1 text-xs text-orange-700">
            Para estes, vale só a regra geral (3.000 km / 50 h por
            apontamento). Não receber alerta aqui não significa estar em dia.
          </p>
          <p className="mt-2 text-sm text-orange-900">
            {painel.sem_plano.map((v) => v.placa).join(" · ")}
          </p>
        </section>
      )}

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Planos ({painel.total})
        </h2>
        <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2">Equipamento</th>
                <th className="px-3 py-2">Revisão</th>
                <th className="px-3 py-2 text-right">Intervalo</th>
                <th className="px-3 py-2 text-right">Última em</th>
                <th className="px-3 py-2 text-right">Leitura atual</th>
                <th className="px-3 py-2 text-right">Próxima</th>
                <th className="px-3 py-2 text-right">Falta</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Ação</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {painel.planos.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-3 py-6 text-center text-slate-500">
                    Nenhum plano cadastrado. Use o formulário abaixo — os
                    intervalos estão na planilha do Bruno (documentos →
                    controle de manutenção → controle de revisões).
                  </td>
                </tr>
              )}
              {painel.planos.map((p) => {
                const b = STATUS_BADGE[p.status] ?? STATUS_BADGE.sem_leitura;
                const u = UNIDADE[p.base] ?? "";
                return (
                  <tr
                    key={p.plano_id}
                    className={p.status === "vencida" ? "bg-red-50" : undefined}
                  >
                    <td className="px-3 py-2 font-medium">
                      <Link
                        href={`/manutencao/veiculos/${p.veiculo_id}`}
                        className="hover:underline"
                      >
                        {p.placa}
                      </Link>
                    </td>
                    <td className="px-3 py-2">{p.descricao}</td>
                    <td className="px-3 py-2 text-right">
                      {num(p.intervalo)} {u}
                    </td>
                    <td className="px-3 py-2 text-right text-slate-600">
                      {num(p.ultima_revisao_marcador)} {u}
                    </td>
                    <td className="px-3 py-2 text-right text-slate-600">
                      {num(p.leitura_atual)} {u}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {num(p.proxima_em)} {u}
                    </td>
                    <td className="px-3 py-2 text-right font-medium">
                      {p.falta === null
                        ? "—"
                        : `${num(p.falta)} ${u}`}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${b.classe}`}
                      >
                        {b.rotulo}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <form action={registrarRevisao} className="flex gap-1">
                        <input type="hidden" name="plano_id" value={p.plano_id} />
                        <input
                          name="marcador"
                          placeholder={p.leitura_atual ?? u}
                          className="w-20 rounded border border-slate-300 px-1 text-xs"
                        />
                        <button
                          type="submit"
                          className="rounded border border-slate-300 px-2 py-1 text-xs whitespace-nowrap"
                        >
                          Revisão feita
                        </button>
                      </form>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <form
        action={criarPlano}
        className="rounded-lg border border-slate-200 bg-white p-4"
      >
        <h2 className="text-sm font-semibold">Novo plano</h2>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-5">
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Equipamento
            </span>
            <select
              name="veiculo_id"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              {veiculos.length === 0 && <option value="">— sem veículos —</option>}
              {veiculos.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.placa} {v.modelo ? `— ${v.modelo}` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Revisão
            </span>
            <input
              name="descricao"
              placeholder="Troca de óleo"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Base
            </span>
            <select
              name="base"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="horas">Horas (máquinas)</option>
              <option value="km">Km (caminhões e carros)</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Intervalo
            </span>
            <input
              name="intervalo"
              placeholder="250"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">
              Última revisão em
            </span>
            <input
              name="ultima_revisao_marcador"
              placeholder="horímetro/km"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
        </div>
        <button
          type="submit"
          className="mt-3 rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
        >
          Cadastrar plano
        </button>
      </form>
    </div>
  );
}
