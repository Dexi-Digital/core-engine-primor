import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function JuridicoPage() {
  return (
    <ModuleStatusCard
      title="Jurídico"
      backendPath="/api/v1/ia"
      scope={[
        "Integração com EasyJur (relatórios e tramitação processual).",
        "Reconhecimento facial (foto Tangerino × fotos de obra) — prova trabalhista.",
        "Alertas de vencimento de contratos e locações.",
        "Auditoria de acesso a documentos sensíveis (LGPD).",
      ]}
    />
  );
}
