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
          nota: "Adapter autentica no sistema real (conta sistemas@primorsolucoes.srv.br; a API é paga). Medido em 19/09: 453 processos e 12.109 andamentos — é o contencioso trabalhista (TRT03 296, TJMG 109), não contratos. A nota anterior afirmava 0 processos: era erro de medição, o endpoint só lista quando recebe acao_listagem=enviar. Endpoints mapeados e desenho fechado em docs/superpowers/specs/2026-09-20-easyjur-processos-design.md; falta construir o pull.",
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
