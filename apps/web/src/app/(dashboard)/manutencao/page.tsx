/**
 * Manutenção & Frota — visão geral com dado real.
 *
 * Era só o cartão de escopo; o dado vivia escondido nas sub-telas. A
 * visão geral agora responde as três perguntas de quem abre o módulo:
 * quantos equipamentos tenho, o que está vencido, e quanto está
 * custando. O cartão de escopo foi para o rodapé.
 */
import Link from "next/link";

import { ModuleStatusCard } from "@/components/ModuleStatusCard";
import { apiFetch } from "@/lib/api";

type Veiculo = { id: number; placa: string; tipo: string; status: string };
type PlanoStatus = {
  plano_id: number;
  placa: string;
  descricao: string;
  base: string;
  falta: string | null;
  status: string;
};
type Painel = {
  total: number;
  por_status: Record<string, number>;
  vencidas: PlanoStatus[];
  sem_plano: { veiculo_id: number; placa: string }[];
};
type Custos = {
  periodo: { inicio: string | null; fim: string | null };
  totais: {
    equipamentos: number;
    apontamentos: number;
    horas: string;
    km: number;
    litros: string;
    custo_total: string;
  };
};

export const dynamic = "force-dynamic";

async function seguro<T>(path: string): Promise<T | null> {
  try {
    return await apiFetch<T>(path);
  } catch {
    return null;
  }
}

function brl(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n)
    ? n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" })
    : "—";
}

function num(v: string | number | null | undefined, casas = 0): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("pt-BR", { maximumFractionDigits: casas }) : "—";
}

const STATUS_VEICULO: Record<string, string> = {
  ativo: "Ativos",
  manutencao: "Em manutenção",
  baixado: "Baixados",
  vendido: "Vendidos",
};

export default async function ManutencaoPage() {
  const [veiculos, painel, custos] = await Promise.all([
    seguro<{ items: Veiculo[]; total: number }>(
      "/api/v1/manutencao-frota/veiculos?page=1&page_size=200",
    ),
    seguro<Painel>("/api/v1/manutencao-frota/planos-manutencao"),
    seguro<Custos>("/api/v1/manutencao-frota/custo-equipamento"),
  ]);

  const falhouTudo = veiculos === null && painel === null && custos === null;
  const porStatus: Record<string, number> = {};
  for (const v of veiculos?.items ?? []) porStatus[v.status] = (porStatus[v.status] ?? 0) + 1;
  const frotaVazia = (veiculos?.total ?? 0) === 0;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Manutenção & Frota</h1>
        <p className="mt-1 text-sm text-slate-500">
          Equipamentos, revisões vencidas e custo apurado a partir das partes
          diárias.
        </p>
      </header>

      {falhouTudo && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">Não consegui carregar os dados da frota.</p>
          <p className="mt-1">Falha de requisição, não base vazia.</p>
        </section>
      )}

      {!falhouTudo && frotaVazia && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">Nenhum equipamento cadastrado ainda.</p>
          <p className="mt-1">
            Tudo abaixo depende do cadastro de veículos. Comece por{" "}
            <Link href="/manutencao/veiculos" className="underline">
              Veículos
            </Link>
            .
          </p>
        </section>
      )}

      <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          ["Equipamentos", veiculos?.total],
          ["Revisões vencidas", painel?.por_status?.vencida],
          ["Sem plano de manutenção", painel?.sem_plano?.length],
          ["Custo apurado no período", custos ? brl(custos.totais.custo_total) : undefined],
        ].map(([rotulo, valor]) => (
          <div key={String(rotulo)} className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">{rotulo}</p>
            <p className="mt-1 text-2xl font-bold tabular-nums">
              {valor === undefined || valor === null
                ? "—"
                : typeof valor === "number"
                  ? valor.toLocaleString("pt-BR")
                  : valor}
            </p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold">Frota por situação</h2>
          {Object.keys(porStatus).length === 0 ? (
            <p className="text-sm text-slate-400">—</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {Object.entries(porStatus).map(([s, n]) => (
                <li key={s} className="flex justify-between">
                  <span className="text-slate-600">{STATUS_VEICULO[s] ?? s}</span>
                  <span className="font-medium tabular-nums">{n}</span>
                </li>
              ))}
            </ul>
          )}
          <Link href="/manutencao/veiculos" className="mt-3 inline-block text-sm text-blue-700 hover:underline">
            Ver cadastro →
          </Link>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold">Planos de manutenção</h2>
          {painel ? (
            <ul className="space-y-1 text-sm">
              {[
                ["vencida", "Vencidas"],
                ["proxima", "Próximas"],
                ["ok", "Em dia"],
                ["sem_leitura", "Sem leitura"],
              ].map(([k, rotulo]) => (
                <li key={k} className="flex justify-between">
                  <span className="text-slate-600">{rotulo}</span>
                  <span className="font-medium tabular-nums">{painel.por_status?.[k] ?? 0}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-400">—</p>
          )}
          <Link href="/manutencao/planos" className="mt-3 inline-block text-sm text-blue-700 hover:underline">
            Ver planos →
          </Link>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold">Uso apurado</h2>
          {custos && custos.totais.apontamentos > 0 ? (
            <ul className="space-y-1 text-sm">
              <li className="flex justify-between"><span className="text-slate-600">Apontamentos</span><span className="font-medium tabular-nums">{num(custos.totais.apontamentos)}</span></li>
              <li className="flex justify-between"><span className="text-slate-600">Horas</span><span className="font-medium tabular-nums">{num(custos.totais.horas, 1)}</span></li>
              <li className="flex justify-between"><span className="text-slate-600">Km</span><span className="font-medium tabular-nums">{num(custos.totais.km)}</span></li>
              <li className="flex justify-between"><span className="text-slate-600">Litros</span><span className="font-medium tabular-nums">{num(custos.totais.litros, 1)}</span></li>
            </ul>
          ) : (
            <p className="text-sm text-slate-400">
              Nenhuma parte diária processada ainda — o custo por equipamento
              nasce delas.
            </p>
          )}
          <Link href="/manutencao/custos" className="mt-3 inline-block text-sm text-blue-700 hover:underline">
            Ver custo por equipamento →
          </Link>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-100 px-4 py-3 text-sm font-semibold">
          Revisões vencidas
        </h2>
        {(painel?.vencidas ?? []).length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-slate-400">
            {painel && painel.total > 0
              ? "Nenhuma revisão vencida."
              : "Nenhum plano cadastrado — sem plano, não há como saber o que está vencido."}
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {(painel?.vencidas ?? []).slice(0, 8).map((p) => (
              <li key={p.plano_id} className="flex gap-4 px-4 py-2 text-sm">
                <span className="w-24 shrink-0 font-medium">{p.placa}</span>
                <span className="min-w-0 flex-1 text-slate-700">{p.descricao}</span>
                <span className="shrink-0 tabular-nums text-red-700">
                  {p.falta !== null ? `${num(Math.abs(Number(p.falta)))} ${p.base === "km" ? "km" : "h"} atrasada` : "—"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {(painel?.sem_plano ?? []).length > 0 && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">
            {painel!.sem_plano.length} equipamento(s) sem plano de manutenção
          </p>
          <p className="mt-1">
            Sem plano, “nenhum alerta” não significa “em dia”. Vale o gatilho
            geral (50 h / 3.000 km), mas não há revisão programada.{" "}
            <Link href="/manutencao/planos" className="underline">
              Cadastrar planos
            </Link>
          </p>
        </section>
      )}

      <ModuleStatusCard
        title="Escopo do módulo"
        backendPath="/api/v1/manutencao-frota"
        scope={[
          {
            label: "Cadastro de veículos com validação de placa, Renavam e chassi.",
            status: "pronto",
          },
          {
            label: "Alertas preventivos por horas/km.",
            status: "pronto",
            nota: "Regra do cliente: 50 h para máquinas, 3.000 km para caminhões e carros.",
          },
          {
            label: "Plano de manutenção por equipamento.",
            status: "pronto",
            nota: "Vencimento medido em uso (horímetro/odômetro), não em calendário. Equipamento sem plano aparece na tela.",
          },
          {
            label: "Custo por equipamento a partir das partes diárias.",
            status: "pronto",
            nota: "A referência é a mediana dos equipamentos do mesmo tipo na própria frota.",
          },
          {
            label: "Leitura automática de partes diárias manuscritas (fotos).",
            status: "mock",
            nota: "Pronto; falta a credencial do Google Document AI.",
          },
          {
            label: "Consulta de IPVA, CRLV e multas.",
            status: "mock",
            nota: "Via Detran/Infosimples; falta o token.",
          },
          {
            label: "Cruzamento do abastecimento apontado × nota fiscal.",
            status: "parcial",
            nota: "O lado apontado em campo já é lido. Falta o lado fiscal: as notas de combustível estão no SharePoint, sem liberação.",
          },
          {
            label: "Sistema 90 como fonte adicional.",
            status: "bloqueado",
            nota: "Depende de acesso e documentação. O objetivo (custo por equipamento) já é entregue pelas partes diárias.",
          },
        ]}
      />
    </div>
  );
}
