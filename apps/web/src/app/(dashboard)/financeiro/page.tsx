import Link from "next/link";

import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function FinanceiroPage() {
  return (
    <div className="flex flex-col gap-6">
      <ModuleStatusCard
        title="Financeiro & Contratos"
        backendPath="/api/v1/financeiro"
        scope={[
          "Ingestão de NF-e (XML parser + OCR de PDFs) e lançamento no TOTVS.",
          "Conciliação automática de remessas e retornos bancários.",
          "Ciclo de contratos (emissão, assinatura digital, AP, alertas de vencimento).",
          "Encaminhamento de NFs das obras para o financeiro com rastreabilidade.",
          "Cruzamento combustível × alimentação × aluguel × descontos em medição.",
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
