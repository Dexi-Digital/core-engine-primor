import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function JuridicoPage() {
  return (
    <ModuleStatusCard
      title="Jurídico"
      backendPath="/api/v1/ia"
      scope={[
        {
          label: "Alertas de vencimento de contratos e locações.",
          status: "pronto",
          nota: "Cron diário; vive no módulo Financeiro.",
        },
        {
          label: "Auditoria de acesso a documentos sensíveis (LGPD).",
          status: "pronto",
          nota: "Registro por (funcionário, fonte) a cada consulta — 764 registrados no último pull.",
        },
        {
          label: "Integração com EasyJur (relatórios e tramitação processual).",
          status: "parcial",
          nota: "Adapter autentica no sistema real por usuário e senha (a API é paga). Medido em 17/09: 1.974 cadastros de pessoas legíveis e 0 processos na base — confirmar com o cliente se o EasyJur é usado para acompanhamento processual antes de construir o pull.",
        },
        {
          label: "Reconhecimento facial (foto Tangerino × fotos de obra).",
          status: "bloqueado",
          nota: "A task é stub. Biometria é dado pessoal sensível (LGPD art. 11) e exige consentimento específico dos funcionários e base legal definida — precisa de decisão do cliente antes de qualquer código, não de desenvolvimento.",
        },
      ]}
    />
  );
}
