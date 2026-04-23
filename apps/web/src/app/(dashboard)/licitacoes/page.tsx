import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function LicitacoesPage() {
  return (
    <ModuleStatusCard
      title="Licitações"
      backendPath="/api/v1/licitacoes"
      scope={[
        "Crawlers B2G: Conlicitação, PNCP, Diários Oficiais.",
        "Acompanhamento de concorrentes (licitantes, vencedores, descontos médios).",
        "Análise de saúde municipal (TCE, transparência, limite de 95% de endividamento).",
        "Busca documental de concorrentes para fase de habilitação.",
        "Adesões a atas de registro de preço com alertas de prazo.",
      ]}
    />
  );
}
