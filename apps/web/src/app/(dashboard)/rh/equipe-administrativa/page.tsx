import Link from "next/link";

import {
  EmptyState,
  KpiGrid,
  PageHeader,
  Section,
  StatCard,
  StatusBadge,
} from "@/components/ui/primitives";
import { apiFetch } from "@/lib/api";

type Employee = {
  id: number;
  cpf: string;
  matricula: string | null;
  nome_completo: string;
  cargo: string;
  setor: string | null;
  obra: string | null;
  email: string | null;
  data_admissao: string | null;
  is_admin_office: boolean;
  status: string;
};

type EmployeeListResponse = {
  items: Employee[];
  total: number;
};

export const dynamic = "force-dynamic";

// Ordem dos setores espelha o organograma do dossie de processos (Bruno
// Zago). Setores nao listados aqui caem em "Outros" no fim da lista, o
// que mantem a UI estavel quando o cliente adicionar um setor novo
// sem precisar de deploy.
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

function formatCpf(cpf: string): string {
  const n = cpf.replace(/\D/g, "");
  if (n.length !== 11) return cpf;
  return `${n.slice(0, 3)}.${n.slice(3, 6)}.${n.slice(6, 9)}-${n.slice(9)}`;
}

async function fetchAdmins(): Promise<EmployeeListResponse | null> {
  try {
    return await apiFetch<EmployeeListResponse>(
      "/api/v1/dp-sesmt/employees?is_admin_office=true&limit=200",
    );
  } catch {
    return null;
  }
}

function groupBySetor(items: Employee[]): Map<string, Employee[]> {
  const groups = new Map<string, Employee[]>();
  for (const emp of items) {
    const key = emp.setor || "Outros";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(emp);
  }
  // Ordena nomes dentro de cada setor pra UI ficar estavel entre reloads.
  for (const list of groups.values()) {
    list.sort((a, b) => a.nome_completo.localeCompare(b.nome_completo));
  }
  return groups;
}

function orderSetores(keys: string[]): string[] {
  const known = SETOR_ORDER.filter((s) => keys.includes(s));
  const unknown = keys
    .filter((s) => !SETOR_ORDER.includes(s))
    .sort((a, b) => a.localeCompare(b));
  return [...known, ...unknown];
}

const STATUS_TONE: Record<string, "success" | "warning" | "default"> = {
  ativo: "success",
  afastado: "warning",
  desligado: "default",
};

export default async function EquipeAdministrativaPage() {
  const data = await fetchAdmins();
  const items = data?.items ?? [];
  const groups = groupBySetor(items);
  const setores = orderSetores(Array.from(groups.keys()));
  const total = data?.total ?? items.length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="RH / Organograma"
        title="Equipe administrativa"
        subtitle="Cadastro dos responsaveis administrativos (organograma do dossie de processos). Clique em qualquer cargo para abrir o cadastro completo no modulo de funcionarios."
        actions={
          <Link
            href="/rh/funcionarios"
            className="chip chip-neutral hover:opacity-80"
          >
            Ver todos os funcionarios →
          </Link>
        }
      />

      <KpiGrid>
        <StatCard
          label="Pessoas administrativas"
          value={total}
          tone="info"
          hint="Filtro is_admin_office=true"
        />
        <StatCard
          label="Setores"
          value={setores.length}
          tone="accent"
          hint={setores.slice(0, 3).join(" · ") + (setores.length > 3 ? " ..." : "")}
        />
        <StatCard
          label="Coordenacoes / Gerencias"
          value={
            items.filter((e) =>
              /coordenador|gerente|diretor/i.test(e.cargo),
            ).length
          }
          tone="success"
        />
        <StatCard
          label="Operacionais (ref.)"
          value="—"
          hint="Ver /rh/funcionarios?is_admin_office=false"
        />
      </KpiGrid>

      {data === null ? (
        <Section title="Erro de conexao">
          <p className="text-sm" style={{ color: "var(--fg-muted)" }}>
            Nao foi possivel conectar a API. Verifique se o backend esta
            no ar.
          </p>
        </Section>
      ) : items.length === 0 ? (
        <EmptyState title="Nenhum administrativo cadastrado">
          O seed do dossie ainda nao foi aplicado. Rode{" "}
          <code>uv run python -m scripts.seed_dossie</code> dentro de
          <code> apps/api</code> ou cadastre manualmente em{" "}
          <Link
            href="/rh/funcionarios"
            className="underline"
            style={{ color: "var(--accent)" }}
          >
            /rh/funcionarios
          </Link>
          .
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-4">
          {setores.map((setor) => {
            const list = groups.get(setor) ?? [];
            return (
              <Section
                key={setor}
                title={
                  <span className="flex items-center gap-2">
                    <span>{setor}</span>
                    <span
                      className="text-xs"
                      style={{ color: "var(--fg-muted)" }}
                    >
                      ({list.length})
                    </span>
                  </span>
                }
              >
                <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {list.map((emp) => {
                    const statusTone = STATUS_TONE[emp.status] ?? "default";
                    return (
                      <li
                        key={emp.id}
                        className="rounded-[var(--r-md)] border p-4"
                        style={{
                          borderColor: "var(--border)",
                          background: "var(--panel)",
                        }}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <div
                              className="font-semibold"
                              style={{ color: "var(--fg)" }}
                            >
                              {emp.nome_completo}
                            </div>
                            <div
                              className="mt-0.5 text-xs"
                              style={{ color: "var(--fg-muted)" }}
                            >
                              {emp.cargo}
                            </div>
                          </div>
                          <StatusBadge tone={statusTone}>
                            {emp.status}
                          </StatusBadge>
                        </div>
                        <dl
                          className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs"
                          style={{ color: "var(--fg-muted)" }}
                        >
                          {emp.matricula ? (
                            <>
                              <dt>Matricula</dt>
                              <dd className="font-mono">{emp.matricula}</dd>
                            </>
                          ) : null}
                          <dt>CPF</dt>
                          <dd className="font-mono">{formatCpf(emp.cpf)}</dd>
                          {emp.email ? (
                            <>
                              <dt>E-mail</dt>
                              <dd className="truncate">{emp.email}</dd>
                            </>
                          ) : null}
                          {emp.data_admissao ? (
                            <>
                              <dt>Admissao</dt>
                              <dd>{emp.data_admissao}</dd>
                            </>
                          ) : null}
                        </dl>
                        <div className="mt-3 flex items-center gap-2">
                          <Link
                            href={`/rh/funcionarios?search=${encodeURIComponent(emp.nome_completo)}`}
                            className="chip chip-neutral hover:opacity-80"
                          >
                            ver / editar →
                          </Link>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </Section>
            );
          })}
        </div>
      )}
    </div>
  );
}
