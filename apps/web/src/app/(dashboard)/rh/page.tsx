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

export const dynamic = "force-dynamic";

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
  const adminsResp = await fetchAdmins();
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

      <ModuleStatusCard
        title="RH / DP & SESMT"
        backendPath="/api/v1/dp-sesmt"
        scope={[
          "Cadastro de funcionarios (manual + dossie via APIs publicas).",
          "Dossie de admissao (CEP via ViaCEP, CNPJ via BrasilAPI, CPF via DirectData).",
          "Onboarding sync com Dominio/Onvio/Tangerino/OnSafety (aguardando credenciais).",
          "Varredura documental OCR do OneDrive (ASOs e docs faltantes).",
          "Acompanhamento de INSS para afastados (alertas e documentos periodicos).",
        ]}
      />

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
