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
          status: "nao_iniciado",
          nota: "Adapter é stub. API não liberada; avaliando RPA como alternativa.",
        },
        {
          label: "Reconhecimento facial (foto Tangerino × fotos de obra).",
          status: "nao_iniciado",
          nota: "A task é stub.",
        },
      ]}
    />
  );
}
