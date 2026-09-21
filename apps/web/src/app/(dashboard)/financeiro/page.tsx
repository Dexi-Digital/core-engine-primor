/**
 * Financeiro & Contratos — visão geral com dado real.
 *
 * Era só o cartão de escopo. O que este módulo tem pronto de verdade é
 * o ciclo de contratos: cadastro, alertas e vencimento. A visão geral
 * mostra isso — quanto está vigente, o que vence em breve, o que já
 * venceu — e o cartão de escopo foi para o rodapé.
 */
import Link from "next/link";

import { ModuleStatusCard } from "@/components/ModuleStatusCard";
import { apiFetch } from "@/lib/api";

type Contrato = {
  id: number;
  titulo: string;
  contraparte_nome: string;
  tipo: string;
  valor: string | null;
  data_inicio: string;
  data_fim: string | null;
  status: string;
  vencimento_status: string | null;
  dias_para_vencer: number | null;
};

export const dynamic = "force-dynamic";

async function seguro<T>(path: string): Promise<T | null> {
  try {
    return await apiFetch<T>(path);
  } catch {
    return null;
  }
}

function brl(v: number): string {
  return v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function dataBr(iso: string | null): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

const VENCIMENTO: Record<string, { rotulo: string; classe: string }> = {
  vencido: { rotulo: "Vencido", classe: "bg-red-100 text-red-700" },
  vencendo: { rotulo: "Vence em breve", classe: "bg-amber-100 text-amber-700" },
  vigente: { rotulo: "Vigente", classe: "bg-emerald-100 text-emerald-700" },
};

export default async function FinanceiroPage() {
  const contratos = await seguro<Contrato[]>("/api/v1/financeiro/contratos");

  const ativos = (contratos ?? []).filter((c) => c.status !== "encerrado");
  const conta = (v: string) => ativos.filter((c) => c.vencimento_status === v).length;
  const valorVigente = ativos
    .filter((c) => c.vencimento_status !== "vencido")
    .reduce((s, c) => s + (Number(c.valor) || 0), 0);
  const proximos = ativos
    .filter((c) => c.dias_para_vencer !== null && c.vencimento_status !== "vencido")
    .sort((a, b) => (a.dias_para_vencer ?? 0) - (b.dias_para_vencer ?? 0))
    .slice(0, 8);
  const vencidos = ativos.filter((c) => c.vencimento_status === "vencido");
  const porTipo: Record<string, number> = {};
  for (const c of ativos) porTipo[c.tipo] = (porTipo[c.tipo] ?? 0) + 1;

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Financeiro & Contratos</h1>
          <p className="mt-1 text-sm text-slate-500">
            Ciclo de contratos: o que está vigente, o que vence em breve e o
            que já venceu. Alerta diário por rotina automática.
          </p>
        </div>
        <Link
          href="/financeiro/contratos"
          className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
        >
          Gerenciar contratos
        </Link>
      </header>

      {contratos === null && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">Não consegui carregar os contratos.</p>
          <p className="mt-1">Falha de requisição, não base vazia.</p>
        </section>
      )}

      {contratos !== null && contratos.length === 0 && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">Nenhum contrato cadastrado ainda.</p>
          <p className="mt-1">
            Os alertas de vencimento só existem para contratos cadastrados.{" "}
            <Link href="/financeiro/contratos" className="underline">
              Cadastrar o primeiro
            </Link>
            .
          </p>
        </section>
      )}

      <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          ["Contratos ativos", contratos ? String(ativos.length) : "—"],
          ["Vencem em breve", contratos ? String(conta("vencendo")) : "—"],
          ["Vencidos", contratos ? String(conta("vencido")) : "—"],
          ["Valor em vigor", contratos ? brl(valorVigente) : "—"],
        ].map(([rotulo, valor]) => (
          <div key={rotulo} className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">{rotulo}</p>
            <p className="mt-1 text-2xl font-bold tabular-nums">{valor}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <div className="rounded-xl border border-slate-200 bg-white md:col-span-2">
          <h2 className="border-b border-slate-100 px-4 py-3 text-sm font-semibold">
            Próximos vencimentos
          </h2>
          {proximos.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-400">
              Nenhum contrato ativo com data de término.
            </p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {proximos.map((c) => {
                const v = VENCIMENTO[c.vencimento_status ?? ""] ?? {
                  rotulo: c.vencimento_status ?? "—",
                  classe: "bg-slate-100 text-slate-700",
                };
                return (
                  <li key={c.id} className="flex items-center gap-4 px-4 py-2 text-sm">
                    <span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${v.classe}`}>
                      {v.rotulo}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{c.titulo}</span>
                      <span className="block truncate text-xs text-slate-500">{c.contraparte_nome}</span>
                    </span>
                    <span className="shrink-0 text-right tabular-nums">
                      <span className="block">{dataBr(c.data_fim)}</span>
                      <span className="block text-xs text-slate-500">
                        {c.dias_para_vencer === 0
                          ? "vence hoje"
                          : `em ${c.dias_para_vencer} dia(s)`}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold">Por tipo</h2>
          {Object.keys(porTipo).length === 0 ? (
            <p className="text-sm text-slate-400">—</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {Object.entries(porTipo).map(([t, n]) => (
                <li key={t} className="flex justify-between">
                  <span className="text-slate-600">{t}</span>
                  <span className="font-medium tabular-nums">{n}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {vencidos.length > 0 && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">{vencidos.length} contrato(s) vencido(s) e ainda ativo(s)</p>
          <ul className="mt-2 space-y-1">
            {vencidos.slice(0, 5).map((c) => (
              <li key={c.id}>
                {c.titulo} — {c.contraparte_nome} — venceu em {dataBr(c.data_fim)}
              </li>
            ))}
          </ul>
        </section>
      )}

      <ModuleStatusCard
        title="Escopo do módulo"
        backendPath="/api/v1/financeiro"
        scope={[
          {
            label: "Ciclo de contratos (cadastro, alertas de vencimento).",
            status: "pronto",
            nota: "Assinatura digital ficou fora do escopo por decisão de 04/08/2026.",
          },
          {
            label: "Leitura de NF-e (XML) e envio à contabilidade.",
            status: "parcial",
            nota: "Leitura pronta; o envio pelo Onvio autentica, mas está desligado por segurança até a validação final.",
          },
          {
            label: "Lançamentos financeiros do TOTVS RM.",
            status: "parcial",
            nota: "Pronto contra ambiente de teste; falta liberação de acesso pelo time Cloud da TOTVS.",
          },
          {
            label: "Leitura de notas fiscais em PDF escaneado.",
            status: "bloqueado",
            nota: "Depende da credencial do Google Document AI — o mesmo bloqueio das partes diárias.",
          },
          {
            label: "Conciliação de remessas e retornos bancários.",
            status: "bloqueado",
            nota: "O formato varia por banco: precisamos de um arquivo de retorno real de exemplo para construir sem adivinhar.",
          },
          {
            label: "Cruzamento combustível × alimentação × aluguel × descontos em medição.",
            status: "bloqueado",
            nota: "Duas das três peças existem. Falta o somatório por obra, que depende dos percentuais de encargos — decisão do cliente.",
          },
        ]}
      />
    </div>
  );
}
