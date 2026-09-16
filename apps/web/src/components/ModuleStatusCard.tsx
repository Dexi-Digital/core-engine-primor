/**
 * Cartao de escopo do modulo, com status POR ITEM.
 *
 * Antes havia um selo fixo "Em desenvolvimento" no topo e a lista de
 * escopo sem marcacao nenhuma. O efeito era subnotificar: Manutencao &
 * Frota, por exemplo, tem 2.883 linhas, 4 tabelas e 5 telas, e quem
 * abria lia "em desenvolvimento" e concluia que nada existia.
 *
 * O oposto tambem importa: item que depende so de credencial (roda em
 * mock hoje) precisa aparecer como tal, senao alguem demonstra achando
 * que e dado real.
 */
export type ItemStatus =
  | "pronto"
  | "parcial"
  | "mock"
  | "nao_iniciado"
  | "sem_status";

export type ScopeItem = {
  label: string;
  status: ItemStatus;
  /** Por que esta nesse estado -- some quando `status === "pronto"`. */
  nota?: string;
};

const ESTILO: Record<ItemStatus, { rotulo: string; classe: string }> = {
  pronto: {
    rotulo: "Pronto",
    classe: "bg-emerald-100 text-emerald-800",
  },
  parcial: {
    rotulo: "Parcial",
    classe: "bg-amber-100 text-amber-800",
  },
  mock: {
    rotulo: "Aguarda credencial",
    classe: "bg-sky-100 text-sky-800",
  },
  nao_iniciado: {
    rotulo: "Não iniciado",
    classe: "bg-slate-200 text-slate-700",
  },
  // Item do formato antigo: sem selo, para nao afirmar o que nao foi
  // verificado.
  sem_status: { rotulo: "", classe: "" },
};

function resumo(itens: ScopeItem[]): { rotulo: string; classe: string } {
  const tipados = itens.filter((i) => i.status !== "sem_status");
  if (tipados.length === 0) {
    return { rotulo: "Em desenvolvimento", classe: "bg-amber-100 text-amber-800" };
  }
  const prontos = tipados.filter((i) => i.status === "pronto").length;
  if (prontos === tipados.length) {
    return { rotulo: "Operacional", classe: "bg-emerald-100 text-emerald-800" };
  }
  if (prontos === 0) {
    return { rotulo: "Em desenvolvimento", classe: "bg-amber-100 text-amber-800" };
  }
  return {
    rotulo: `${prontos} de ${tipados.length} prontos`,
    classe: "bg-sky-100 text-sky-800",
  };
}

type Props = {
  title: string;
  /**
   * Aceita string simples (sem status) ou `ScopeItem`. O formato antigo
   * segue valendo de proposito: rotular status de modulo que ninguem
   * verificou seria trocar uma informacao errada por outra.
   */
  scope: (string | ScopeItem)[];
  backendPath: string;
};

function normalizar(scope: (string | ScopeItem)[]): ScopeItem[] {
  return scope.map((s) =>
    typeof s === "string" ? { label: s, status: "sem_status" as const } : s,
  );
}

export function ModuleStatusCard({ title, scope, backendPath }: Props) {
  const itens = normalizar(scope);
  const geral = resumo(itens);
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <header className="flex items-start justify-between gap-4">
        <h1 className="text-2xl font-bold">{title}</h1>
        <span
          className={`shrink-0 rounded-full px-3 py-1 text-xs font-semibold ${geral.classe}`}
        >
          {geral.rotulo}
        </span>
      </header>
      <p className="mt-2 text-sm text-slate-500">
        Endpoint backend: <code>{backendPath}</code>
      </p>
      <h2 className="mt-6 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Escopo deste módulo
      </h2>
      <ul className="mt-3 space-y-2 text-sm text-slate-700">
        {itens.map((item) => {
          const e = ESTILO[item.status];
          return (
            <li key={item.label} className="flex flex-wrap items-start gap-2">
              {e.rotulo ? (
                <span
                  className={`mt-0.5 shrink-0 rounded px-2 py-0.5 text-xs font-medium ${e.classe}`}
                >
                  {e.rotulo}
                </span>
              ) : null}
              <span className="flex-1 min-w-[12rem]">
                {item.label}
                {item.nota ? (
                  <span className="block text-xs text-slate-500">
                    {item.nota}
                  </span>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
