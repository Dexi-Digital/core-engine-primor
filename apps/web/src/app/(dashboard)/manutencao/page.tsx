import Link from "next/link";

import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function ManutencaoPage() {
  return (
    <div className="flex flex-col gap-6">
      <ModuleStatusCard
        title="Manutenção & Frota"
        backendPath="/api/v1/manutencao-frota"
        scope={[
          "Cadastro de veículos com validação local (placa Mercosul/antiga, Renavam DV, chassi ISO 3779).",
          "OCR de partes diárias manuscritas (fotos de campo).",
          "Cruzamento telemetria × NF (combustível, locação, descontos em medição).",
          "RPA despachante: IPVA, CRLV, certidões negativas, multas.",
          "Integração com Sistema 90 para apropriação de custo por equipamento.",
          "Alertas preventivos por horas/km e plano de manutenção.",
        ]}
      />
      <nav className="flex flex-wrap gap-3">
        <Link
          href="/manutencao/veiculos"
          className="rounded-md border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-slate-400 hover:text-slate-900"
        >
          → Cadastro de veículos
        </Link>
        <Link
          href="/manutencao/partes-diarias"
          className="rounded-md border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-slate-400 hover:text-slate-900"
        >
          → Partes diárias (OCR)
        </Link>
      </nav>
    </div>
  );
}
