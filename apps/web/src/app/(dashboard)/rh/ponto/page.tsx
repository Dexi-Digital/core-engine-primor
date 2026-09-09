import { apiFetch } from "@/lib/api";
import {
  EmptyState,
  KpiGrid,
  PageHeader,
  Section,
  StatCard,
} from "@/components/ui/primitives";
import { SubNav, type SubNavItem } from "@/components/ui/sub-nav";

const RH_SUBNAV: SubNavItem[] = [
  { href: "/rh", label: "Visão geral" },
  { href: "/rh/funcionarios", label: "Funcionários" },
  { href: "/rh/equipe-administrativa", label: "Equipe administrativa" },
  { href: "/rh/afastamentos", label: "Afastamentos" },
  { href: "/rh/ponto", label: "Ponto" },
];

type Resumo = {
  locais: {
    total: number;
    com_obra: number;
    obra_nao_cadastrada: number;
    administrativos: number;
  };
  funcionarios: { ativos: number; sem_cadastro_dp: number };
  batidas: { total: number };
  execucoes: {
    recurso: string;
    source: string;
    janela: string;
    status: string;
    lidos: number;
    gravados: number;
    error_message: string | null;
    started_at: string | null;
  }[];
};

type Local = {
  id: number;
  nome: string;
  ativo: boolean;
  codigo_obra: string | null;
  obra_nome: string | null;
  vinculo: "ligado" | "obra_nao_cadastrada" | "administrativo";
};

type Funcionario = {
  id: number;
  nome: string | null;
  local_trabalho_nome: string | null;
  data_admissao: string | null;
  no_cadastro_dp: boolean;
};

export const dynamic = "force-dynamic";

async function carregar<T>(rota: string): Promise<T | null> {
  try {
    return await apiFetch<T>(rota);
  } catch {
    return null;
  }
}

function formatarData(valor: string | null): string {
  if (!valor) return "—";
  return new Date(valor).toLocaleDateString("pt-BR");
}

export default async function PontoPage() {
  const [resumo, locais, semCadastro] = await Promise.all([
    carregar<Resumo>("/api/v1/ponto/resumo"),
    carregar<Local[]>("/api/v1/ponto/locais"),
    carregar<Funcionario[]>("/api/v1/ponto/funcionarios?apenas_sem_cadastro=true"),
  ]);

  if (!resumo) {
    return (
      <div>
        <PageHeader
          eyebrow="RH / DP"
          title="Ponto eletrônico"
          subtitle="Fonte: Sólides (Tangerino)."
        />
        <SubNav items={RH_SUBNAV} />
        <EmptyState title="Não foi possível falar com a API">
          Verifique se o backend está no ar.
        </EmptyState>
      </div>
    );
  }

  const pendencias =
    resumo.locais.obra_nao_cadastrada + resumo.funcionarios.sem_cadastro_dp;

  return (
    <div>
      <PageHeader
        eyebrow="RH / DP"
        title="Ponto eletrônico"
        subtitle={
          <>
            Fonte: <strong>Sólides (Tangerino)</strong>. Somente leitura — o
            cadastro de funcionários continua sendo o do DP.
          </>
        }
      />

      <SubNav items={RH_SUBNAV} />

      <KpiGrid>
        <StatCard
          label="Funcionários ativos"
          value={resumo.funcionarios.ativos}
          hint="no ponto"
        />
        <StatCard
          label="Locais de trabalho"
          value={resumo.locais.total}
          hint={`${resumo.locais.com_obra} ligados a obras`}
          tone={resumo.locais.com_obra > 0 ? "success" : "default"}
        />
        <StatCard
          label="Batidas importadas"
          value={resumo.batidas.total}
        />
        <StatCard
          label="Pendências"
          value={pendencias}
          hint="precisam de ação humana"
          tone={pendencias > 0 ? "warning" : "success"}
        />
      </KpiGrid>

      <Section title="Locais de trabalho sem obra cadastrada">
        {resumo.locais.obra_nao_cadastrada === 0 ? (
          <EmptyState title="Nenhuma pendência">
            Todos os locais que representam obra estão ligados.
          </EmptyState>
        ) : (
          <>
            <p className="mb-3 text-sm" style={{ color: "var(--fg-muted)" }}>
              O local existe no Sólides e o código da obra foi identificado no
              nome, mas essa obra ainda não está cadastrada no Motor Central.
              Cadastrar a obra com o código correspondente resolve o vínculo
              automaticamente na próxima importação.
            </p>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead
                  className="text-left text-xs uppercase tracking-wide"
                  style={{ color: "var(--fg-subtle)" }}
                >
                  <tr>
                    <th className="py-2 pr-4">Local no Sólides</th>
                    <th className="py-2 pr-4">Código identificado</th>
                    <th className="py-2 pr-4">Situação</th>
                  </tr>
                </thead>
                <tbody>
                  {(locais ?? [])
                    .filter((l) => l.vinculo === "obra_nao_cadastrada")
                    .map((l) => (
                      <tr key={l.id} style={{ borderTop: "1px solid var(--border)" }}>
                        <td className="py-2 pr-4">{l.nome}</td>
                        <td className="py-2 pr-4">
                          <code>{l.codigo_obra}</code>
                        </td>
                        <td className="py-2 pr-4">
                          <span className="chip chip-warning">
                            obra não cadastrada
                          </span>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>

      <Section title="Funcionários do ponto sem cadastro no DP">
        {resumo.funcionarios.sem_cadastro_dp === 0 ? (
          <EmptyState title="Nenhuma pendência">
            Todos os funcionários ativos do ponto têm cadastro no DP.
          </EmptyState>
        ) : (
          <>
            <p className="mb-3 text-sm" style={{ color: "var(--fg-muted)" }}>
              Batem ponto no Sólides mas o CPF não existe no cadastro do DP. A
              importação nunca cria funcionário — o cadastro é a fonte da
              verdade, então isso se resolve cadastrando a pessoa.
            </p>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead
                  className="text-left text-xs uppercase tracking-wide"
                  style={{ color: "var(--fg-subtle)" }}
                >
                  <tr>
                    <th className="py-2 pr-4">Nome</th>
                    <th className="py-2 pr-4">Local de trabalho</th>
                    <th className="py-2 pr-4">Admissão</th>
                  </tr>
                </thead>
                <tbody>
                  {(semCadastro ?? []).map((f) => (
                    <tr key={f.id} style={{ borderTop: "1px solid var(--border)" }}>
                      <td className="py-2 pr-4">{f.nome ?? "—"}</td>
                      <td className="py-2 pr-4">
                        {f.local_trabalho_nome ?? "—"}
                      </td>
                      <td className="py-2 pr-4">
                        {formatarData(f.data_admissao)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>

      <Section title="Últimas importações">
        {resumo.execucoes.length === 0 ? (
          <EmptyState title="Nenhuma importação registrada">
            A importação roda automaticamente às 2h30.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead
                className="text-left text-xs uppercase tracking-wide"
                style={{ color: "var(--fg-subtle)" }}
              >
                <tr>
                  <th className="py-2 pr-4">Quando</th>
                  <th className="py-2 pr-4">O quê</th>
                  <th className="py-2 pr-4">Origem</th>
                  <th className="py-2 pr-4">Período</th>
                  <th className="py-2 pr-4 text-right">Registros</th>
                  <th className="py-2 pr-4">Situação</th>
                </tr>
              </thead>
              <tbody>
                {resumo.execucoes.map((e, i) => (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td className="py-2 pr-4">{formatarData(e.started_at)}</td>
                    <td className="py-2 pr-4">{e.recurso}</td>
                    <td className="py-2 pr-4">
                      {e.source === "beat" ? "automática" : "manual"}
                    </td>
                    <td className="py-2 pr-4">{e.janela}</td>
                    <td className="py-2 pr-4 text-right">{e.lidos}</td>
                    <td className="py-2 pr-4">
                      <span
                        className={
                          e.status === "ok"
                            ? "chip chip-success"
                            : "chip chip-danger"
                        }
                      >
                        {e.status === "ok" ? "concluída" : "falhou"}
                      </span>
                      {e.error_message ? (
                        <div
                          className="mt-1 text-xs"
                          style={{ color: "var(--danger)" }}
                        >
                          {e.error_message}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
