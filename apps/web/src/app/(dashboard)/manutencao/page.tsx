import Link from "next/link";

import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function ManutencaoPage() {
  return (
    <div className="flex flex-col gap-6">
      <ModuleStatusCard
        title="Manutenção & Frota"
        backendPath="/api/v1/manutencao-frota"
        scope={[
          {
            label:
              "Cadastro de veículos com validação local (placa Mercosul/antiga, Renavam DV, chassi ISO 3779).",
            status: "pronto",
          },
          {
            label: "OCR de partes diárias manuscritas (fotos de campo).",
            status: "mock",
            nota: "Pipeline pronto; falta a credencial do Google Document AI.",
          },
          {
            label: "Alertas preventivos por horas/km.",
            status: "pronto",
            nota: "Regra do cliente: 50h para máquinas, 3.000 km para caminhões e carros.",
          },
          {
            label: "Plano de manutenção (revisões programadas por equipamento).",
            status: "nao_iniciado",
            nota: "Hoje vive na planilha do SharePoint (documentos → controle de revisões).",
          },
          {
            label: "Consulta de IPVA, CRLV e multas.",
            status: "mock",
            nota: "Via Detran/Infosimples; falta o token. Resolve a multa que chega fora do prazo de identificação.",
          },
          {
            label:
              "Integração com Sistema 90 para apropriação de custo por equipamento.",
            status: "nao_iniciado",
            nota: "Adapter é stub; falta acesso e documentação da API.",
          },
          {
            label:
              "Cruzamento telemetria × NF (combustível, locação, descontos em medição).",
            status: "nao_iniciado",
            nota: "Falta definir a origem da telemetria.",
          },
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
