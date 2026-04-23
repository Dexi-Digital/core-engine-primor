import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function RHPage() {
  return (
    <ModuleStatusCard
      title="RH / DP & SESMT"
      backendPath="/api/v1/dp-sesmt"
      scope={[
        "Dossiê de admissão (antecedentes, INSS, benefícios, empresas anteriores).",
        "Varredura documental OCR do OneDrive (ASOs e docs faltantes).",
        "Onboarding automático: cadastro único → Domínio, Onvio, Tangerino, OnSafety.",
        "E-learning via WhatsApp com assinatura digital e emissão de certificado.",
        "Acompanhamento de INSS para afastados (alertas e documentos periódicos).",
      ]}
    />
  );
}
