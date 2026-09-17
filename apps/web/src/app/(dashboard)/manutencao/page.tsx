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
            status: "pronto",
            nota: "Intervalo por equipamento, vencimento medido em uso (horímetro/odômetro) e registro de revisão feita. Equipamento sem plano aparece na tela — ausência de alerta não é sinal de estar em dia.",
          },
          {
            label: "Consulta de IPVA, CRLV e multas.",
            status: "mock",
            nota: "Via Detran/Infosimples; falta o token. Resolve a multa que chega fora do prazo de identificação.",
          },
          {
            label:
              "Apropriação e custo por equipamento: consumo (L/h e km/L), custo por hora e por km, e quem está fora da curva da própria frota.",
            status: "pronto",
            nota: "Calculado a partir das partes diárias. A referência é a mediana dos equipamentos do mesmo tipo — não um número de catálogo, que ignoraria terreno e operador.",
          },
          {
            label:
              "Cruzamento do abastecimento apontado × nota fiscal (combustível, locação, descontos em medição).",
            status: "parcial",
            nota: "O lado apontado em campo já é lido e comparado entre equipamentos. Falta o lado fiscal: as notas de combustível estão na planilha do SharePoint, ainda sem liberação.",
          },
          {
            label:
              "Integração com o Sistema 90 como fonte adicional de apropriação.",
            status: "bloqueado",
            nota: "O adapter é um stub — nada foi construído. Depende de acesso e documentação da API. O objetivo deste item (custo por equipamento) já é entregue pelas partes diárias, acima.",
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
          href="/manutencao/planos"
          className="rounded-md border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-slate-400 hover:text-slate-900"
        >
          Planos de manutenção
        </Link>
        <Link
          href="/manutencao/custos"
          className="rounded-md border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-slate-400 hover:text-slate-900"
        >
          Custo por equipamento
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
