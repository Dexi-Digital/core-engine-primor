import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function FinanceiroPage() {
  return (
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
  );
}
