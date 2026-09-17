import Link from "next/link";

import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function FinanceiroPage() {
  return (
    <div className="flex flex-col gap-6">
      <ModuleStatusCard
        title="Financeiro & Contratos"
        backendPath="/api/v1/financeiro"
        scope={[
          {
            label: "Ciclo de contratos (cadastro, AP, alertas de vencimento).",
            status: "pronto",
            nota: "Assinatura digital ficou fora do escopo por decisão de 04/08/2026.",
          },
          {
            label: "Ingestão de NF-e (parser de XML) e envio à contabilidade.",
            status: "parcial",
            nota: "Parser e módulo fiscal prontos (1.894 linhas); envio pelo Onvio autentica, mas ONVIO_ALLOW_SEND está desligado.",
          },
          {
            label: "Lançamentos financeiros do TOTVS RM.",
            status: "parcial",
            nota: "Adapter pronto contra mock; falta liberação de portas/IP pelo time Cloud da TOTVS.",
          },
          {
            label: "OCR de PDFs de nota fiscal.",
            status: "nao_iniciado",
            nota: "A task de parse é stub.",
          },
          {
            label: "Conciliação automática de remessas e retornos bancários.",
            status: "nao_iniciado",
            nota: "A task é stub.",
          },
          {
            label: "Cruzamento combustível × alimentação × aluguel × descontos em medição.",
            status: "nao_iniciado",
            nota: "Depende dos percentuais de encargos, ainda não recebidos.",
          },
        ]}
      />
      <nav className="flex flex-wrap gap-3">
        <Link
          href="/financeiro/contratos"
          className="rounded-md border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-slate-400 hover:text-slate-900"
        >
          → Contratos
        </Link>
      </nav>
    </div>
  );
}
