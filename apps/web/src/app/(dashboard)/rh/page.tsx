import Link from "next/link";

import { ModuleStatusCard } from "@/components/ModuleStatusCard";
import {
  KpiGrid,
  PageHeader,
  Section,
  StatCard,
  StatusBadge,
} from "@/components/ui/primitives";
import { apiFetch } from "@/lib/api";
import { SubNav } from "@/components/ui/sub-nav";
import { RH_SUBNAV } from "./subnav";


type Employee = {
  id: number;
  nome_completo: string;
  cargo: string;
  setor: string | null;
  matricula: string | null;
  status: string;
  is_admin_office: boolean;
};

type EmployeeListResponse = {
  items: Employee[];
  total: number;
};

// Demanda #2: marcos do contrato de experiencia (GET /experiencia/prazos).
type PrazoExperiencia = {
  employee_id: number;
  nome_completo: string;
  cargo: string | null;
  obra: string | null;
  tipo_contrato: string | null;
  data_admissao: string;
  primeiro_periodo_dias: number;
  segundo_periodo_dias: number;
  periodos_assumidos: boolean;
  fim_primeiro_periodo: string;
  fim_experiencia: string;
  origem: string;
  proximo_marco: "prorrogacao" | "efetivacao" | null;
  data_proximo_marco: string | null;
  dias_restantes: number | null;
};

// Mesmo horizonte da maior janela de alerta (15 dias) com folga para o
// RH se programar.
const EXPERIENCIA_HORIZONTE_DIAS = 30;

export const dynamic = "force-dynamic";

async function fetchPrazosExperiencia(): Promise<PrazoExperiencia[] | null> {
  try {
    return await apiFetch<PrazoExperiencia[]>(
      `/api/v1/dp-sesmt/experiencia/prazos?horizonte_dias=${EXPERIENCIA_HORIZONTE_DIAS}`,
    );
  } catch {
    return null;
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  // Data pura (YYYY-MM-DD): evita o deslocamento de fuso do `new Date`.
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

function prazoTone(dias: number | null): "danger" | "warning" | "info" {
  if (dias === null || dias <= 7) return "danger";
  if (dias <= 15) return "warning";
  return "info";
}

// Mesma ordem do organograma do dossie usada em /rh/equipe-administrativa.
const SETOR_ORDER = [
  "Diretoria",
  "Planejamento",
  "Licitacoes",
  "Contratos",
  "Orcamento",
  "Comercial",
  "TI",
  "Administrativo",
  "Consultoria",
];

async function fetchAdmins(): Promise<EmployeeListResponse | null> {
  try {
    return await apiFetch<EmployeeListResponse>(
      "/api/v1/dp-sesmt/employees?is_admin_office=true&limit=200",
    );
  } catch {
    return null;
  }
}

function countBySetor(items: Employee[]): Array<[string, Employee[]]> {
  const groups = new Map<string, Employee[]>();
  for (const emp of items) {
    const key = emp.setor || "Outros";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(emp);
  }
  const ordered = SETOR_ORDER.filter((s) => groups.has(s)).map(
    (s) => [s, groups.get(s)!] as [string, Employee[]],
  );
  const extras = Array.from(groups.keys())
    .filter((s) => !SETOR_ORDER.includes(s))
    .sort((a, b) => a.localeCompare(b))
    .map((s) => [s, groups.get(s)!] as [string, Employee[]]);
  return [...ordered, ...extras];
}

export default async function RHPage() {
  const [adminsResp, prazosExperiencia] = await Promise.all([
    fetchAdmins(),
    fetchPrazosExperiencia(),
  ]);
  const admins = adminsResp?.items ?? [];
  const adminGroups = countBySetor(admins);
  const totalAdmins = adminsResp?.total ?? admins.length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Modulo A"
        title="RH / DP & SESMT"
        subtitle="Cadastro de funcionarios (operacionais + equipe administrativa), dossie de admissao via APIs publicas, alertas ASO e acompanhamento INSS."
      />

      <SubNav items={RH_SUBNAV} />

      <ModuleStatusCard
        title="RH / DP & SESMT"
        backendPath="/api/v1/dp-sesmt"
        scope={[
          {
            label: "Cadastro de funcionários (manual + dossiê via APIs públicas).",
            status: "pronto",
            nota: "930 pessoas reais importadas da OnSafety.",
          },
          {
            label:
              "Jornada de admissão: verifica pendências, gera o kit para a contabilidade (inclusive em lote por obra) e registra entrega e confirmação.",
            status: "pronto",
            nota: "POST /dp-sesmt/onboarding era um stub que descartava o payload; agora abre a jornada de verdade.",
          },
          {
            label: "Acompanhamento de INSS para afastados (alertas e documentos periódicos).",
            status: "pronto",
          },
          {
            label:
              "Alerta de fim de contrato de experiência (fim do 1º período e fim dos 90 dias), com notificação no sino.",
            status: "pronto",
            nota: "Quando o cadastro não informa a divisão dos períodos, assume 45+45 — regra a confirmar com o DP.",
          },
          {
            label: "Dossiê de admissão: consulta de CEP (ViaCEP) e CNPJ (BrasilAPI).",
            status: "pronto",
            nota: "Fontes públicas, sem credencial. Toda consulta fica registrada na auditoria com quem consultou.",
          },
          {
            label: "Dossiê de admissão: consulta de CPF (DirectData).",
            status: "bloqueado",
            nota: "Código pronto; aguarda a Primor contratar o DirectData (custo por consulta). Exige justificativa (LGPD) a cada consulta.",
          },
          {
            label: "Pull de ASO, EPI e treinamentos da OnSafety.",
            status: "pronto",
            nota: "374 ASOs e 5.558 fichas de EPI no ar, com validade.",
          },
          {
            label: "Onboarding sync: push de funcionário para a OnSafety.",
            status: "parcial",
            nota: "Código pronto, mas o token é de PRODUÇÃO e a escrita está bloqueada por guard — ligar significa criar trabalhador na base real do cliente.",
          },
          {
            label: "Varredura documental OCR do OneDrive (ASOs e docs faltantes).",
            status: "bloqueado",
            nota: "A leitura por OCR ainda não foi construída. Depende de dois acessos da TI da Primor: leitura do site do SharePoint onde ficam os documentos de RH/DP (hoje negado) e o recurso de OCR da Microsoft (Azure AI Document Intelligence, após aprovação do custo). Enquanto isso, ASOs vêm do pull da OnSafety e o % de atendimento documental já sai no Diagnóstico.",
          },
        ]}
      />

      <Section
        title={`Contratos de experiência — marcos nos próximos ${EXPERIENCIA_HORIZONTE_DIAS} dias`}
      >
        {prazosExperiencia === null ? (
          <p className="text-sm" style={{ color: "var(--fg-muted)" }}>
            Nao foi possivel conectar a API.
          </p>
        ) : prazosExperiencia.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--fg-muted)" }}>
            Nenhum contrato de experiência com prazo nos próximos{" "}
            {EXPERIENCIA_HORIZONTE_DIAS} dias.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead
                className="border-b text-[11px] uppercase tracking-[0.1em]"
                style={{ borderColor: "var(--border)", color: "var(--fg-muted)" }}
              >
                <tr>
                  <th className="py-2 pr-3">Funcionário</th>
                  <th className="py-2 pr-3">Admissão</th>
                  <th className="py-2 pr-3">Decisão</th>
                  <th className="py-2 pr-3">Data</th>
                  <th className="py-2 pr-3 text-right">Prazo</th>
                </tr>
              </thead>
              <tbody>
                {prazosExperiencia.map((p) => (
                  <tr
                    key={p.employee_id}
                    className="border-b"
                    style={{ borderColor: "var(--border)" }}
                  >
                    <td className="py-2 pr-3">
                      <Link
                        href={`/rh/funcionarios/${p.employee_id}`}
                        className="hover:underline"
                        style={{ color: "var(--fg)" }}
                      >
                        {p.nome_completo}
                      </Link>
                      <div className="text-[11px]" style={{ color: "var(--fg-muted)" }}>
                        {p.cargo ?? "—"} · {p.obra ?? "sem obra"}
                      </div>
                    </td>
                    <td className="py-2 pr-3">{formatDate(p.data_admissao)}</td>
                    <td className="py-2 pr-3">
                      {p.proximo_marco === "prorrogacao"
                        ? `Prorrogar? (fim do 1º período, ${p.primeiro_periodo_dias}d)`
                        : `Efetivar ou desligar (fim dos ${p.primeiro_periodo_dias + p.segundo_periodo_dias}d)`}
                      {p.periodos_assumidos ? (
                        <div className="text-[11px]" style={{ color: "var(--fg-muted)" }}>
                          45+45 assumido — períodos não informados no cadastro
                        </div>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3">{formatDate(p.data_proximo_marco)}</td>
                    <td className="py-2 pr-3 text-right">
                      <StatusBadge tone={prazoTone(p.dias_restantes)}>
                        {p.dias_restantes === 0 ? "hoje" : `${p.dias_restantes} dia(s)`}
                      </StatusBadge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        title="Equipe administrativa (organograma do dossie)"
        action={
          <Link
            href="/rh/equipe-administrativa"
            className="chip chip-accent hover:opacity-80"
          >
            Ver pagina completa →
          </Link>
        }
      >
        {adminsResp === null ? (
          <p
            className="text-sm"
            style={{ color: "var(--fg-muted)" }}
          >
            Nao foi possivel conectar a API.
          </p>
        ) : totalAdmins === 0 ? (
          <p
            className="text-sm"
            style={{ color: "var(--fg-muted)" }}
          >
            Nenhum administrativo cadastrado. Rode{" "}
            <code>uv run python -m scripts.seed_dossie</code> em{" "}
            <code>apps/api</code> para popular o organograma do dossie.
          </p>
        ) : (
          <>
            <KpiGrid>
              <StatCard
                label="Pessoas administrativas"
                value={totalAdmins}
                tone="info"
              />
              <StatCard
                label="Setores ocupados"
                value={adminGroups.length}
                tone="accent"
                hint="Inclui diretoria + consultoria"
              />
              <StatCard
                label="Coordenacoes / gerencias"
                value={
                  admins.filter((e) =>
                    /coordenador|gerente|diretor/i.test(e.cargo),
                  ).length
                }
                tone="success"
              />
              <StatCard
                label="Em alerta"
                value={
                  admins.filter((e) => e.status !== "ativo").length
                }
                tone="warning"
                hint="Afastados ou desligados"
              />
            </KpiGrid>

            <ul className="mt-5 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {adminGroups.map(([setor, list]) => (
                <li
                  key={setor}
                  className="rounded-[var(--r-md)] border p-4"
                  style={{
                    borderColor: "var(--border)",
                    background: "var(--panel)",
                  }}
                >
                  <div className="flex items-center justify-between">
                    <div
                      className="text-[11px] font-semibold uppercase tracking-[0.1em]"
                      style={{ color: "var(--fg-muted)" }}
                    >
                      {setor}
                    </div>
                    <StatusBadge tone="info" dot={false}>
                      {list.length}
                    </StatusBadge>
                  </div>
                  <ul className="mt-2 flex flex-col gap-1 text-sm">
                    {list.slice(0, 4).map((emp) => (
                      <li key={emp.id} className="flex items-start gap-2">
                        <Link
                          href={`/rh/funcionarios?search=${encodeURIComponent(emp.nome_completo)}`}
                          className="truncate hover:underline"
                          style={{ color: "var(--fg)" }}
                          title={`${emp.nome_completo} - ${emp.cargo}`}
                        >
                          {emp.nome_completo}
                        </Link>
                        <span
                          className="ml-auto truncate text-[11px]"
                          style={{ color: "var(--fg-muted)" }}
                        >
                          {emp.cargo}
                        </span>
                      </li>
                    ))}
                    {list.length > 4 ? (
                      <li>
                        <Link
                          href={`/rh/equipe-administrativa#${encodeURIComponent(setor.toLowerCase())}`}
                          className="text-xs underline"
                          style={{ color: "var(--accent)" }}
                        >
                          + {list.length - 4} pessoa(s)
                        </Link>
                      </li>
                    ) : null}
                  </ul>
                </li>
              ))}
            </ul>
          </>
        )}
      </Section>

      <Section title="Atalhos">
        <ul className="flex flex-col gap-1 text-sm">
          <li>
            <Link
              href="/rh/equipe-administrativa"
              className="hover:underline"
              style={{ color: "var(--fg)" }}
            >
              → Equipe administrativa (organograma do dossie)
            </Link>
          </li>
          <li>
            <Link
              href="/rh/funcionarios"
              className="hover:underline"
              style={{ color: "var(--fg)" }}
            >
              → Funcionarios (cadastro + dossie de admissao)
            </Link>
          </li>
          <li>
            <Link
              href="/rh/afastamentos"
              className="hover:underline"
              style={{ color: "var(--fg)" }}
            >
              → Afastamentos INSS (DCB + pericia)
            </Link>
          </li>
        </ul>
      </Section>
    </div>
  );
}
