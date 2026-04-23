import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function ManutencaoPage() {
  return (
    <ModuleStatusCard
      title="Manutenção & Frota"
      backendPath="/api/v1/manutencao-frota"
      scope={[
        "OCR de partes diárias manuscritas (fotos de campo).",
        "Cruzamento telemetria × NF (combustível, locação, descontos em medição).",
        "RPA despachante: IPVA, CRLV, certidões negativas, multas.",
        "Integração com Sistema 90 para apropriação de custo por equipamento.",
        "Alertas preventivos por horas/km e plano de manutenção.",
      ]}
    />
  );
}
