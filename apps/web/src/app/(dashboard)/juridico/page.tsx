/**
 * Jurídico — o contencioso da Primor, espelhado do EasyJur.
 *
 * Esta tela era só um cartão de status. Agora mostra os processos e os
 * andamentos de verdade; o cartão de escopo foi para o rodapé.
 *
 * Regra da tela: campo que o escritório preenche por exceção (risco,
 * fase, resultado, tipo de ação) aparece SEMPRE com "X de Y
 * preenchidos". Mostrar só a distribuição faria 11% de cobertura
 * parecer uma afirmação sobre a carteira inteira.
 */
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { AutoRefresh } from "@/components/AutoRefresh";
import { ModuleStatusCard } from "@/components/ModuleStatusCard";
import { ApiError, apiFetch } from "@/lib/api";

type Processo = {
  id: number;
  numero_cnj: string | null;
  status: string | null;
  area: string | null;
  tribunal: string | null;
  instancia: string | null;
  comarca: string | null;
  titulo: string | null;
  cliente: string | null;
  contrario: string | null;
  risco: string | null;
  fase_atual: string | null;
  codigo_obra: string | null;
  obra_id: number | null;
};

type Andamento = {
  id: number;
  numero_cnj: string;
  tipo: string | null;
  status: string | null;
  descricao: string | null;
  data: string | null;
};

type Esparso = {
  preenchidos: number;
  total: number;
  valores: Record<string, number>;
};

type Resumo = {
  total: number;
  andamentos: number;
  por_status: Record<string, number>;
  por_area: Record<string, number>;
  por_tribunal: Record<string, number>;
  com_obra: number;
  campos_esparsos: Record<string, Esparso>;
  ultimo_sync: {
    executado_em: string | null;
    source: string;
    processos: number;
    andamentos: number;
    total_declarado: number | null;
    divergencia: boolean;
    status: "em_andamento" | "ok" | "erro";
    erro: string | null;
    iniciado_em: string | null;
  } | null;
};

export const dynamic = "force-dynamic";

const ESPARSO_ROTULO: Record<string, string> = {
  risco: "Risco",
  resultado: "Resultado",
  fase_atual: "Fase atual",
  tipo_acao: "Tipo de ação",
};

async function seguro<T>(path: string): Promise<T | null> {
  try {
    return await apiFetch<T>(path);
  } catch {
    return null;
  }
}

async function sincronizar(): Promise<void> {
  "use server";
  let erro: string | null = null;
  try {
    await apiFetch("/api/v1/juridico/sync", { method: "POST" });
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    // 503 = credencial ou fila fora; 409 = ja esta rodando. O motivo
    // vai para a URL e aparece na tela -- a primeira versao engolia o
    // erro e recarregava igual, e "cliquei e nada mudou" era
    // indistinguivel de "rodou".
    try {
      erro = (JSON.parse(e.body) as { detail?: string }).detail ?? e.body;
    } catch {
      erro = e.body || `HTTP ${e.status}`;
    }
  }
  revalidatePath("/juridico");
  redirect(erro ? `/juridico?erro=${encodeURIComponent(erro)}` : "/juridico");
}

function minutosDesde(iso: string | null): number | null {
  if (!iso) return null;
  return Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
}

function dataBr(iso: string | null): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

function quando(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
    dateStyle: "short",
    timeStyle: "short",
  });
}

function Distribuicao({ dados }: { dados: Record<string, number> }) {
  const itens = Object.entries(dados).slice(0, 6);
  if (itens.length === 0) return <p className="text-sm text-slate-400">—</p>;
  return (
    <ul className="space-y-1 text-sm">
      {itens.map(([k, n]) => (
        <li key={k} className="flex justify-between gap-3">
          <span className="truncate text-slate-600">{k}</span>
          <span className="font-medium tabular-nums">{n}</span>
        </li>
      ))}
    </ul>
  );
}

export default async function JuridicoPage(props: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await props.searchParams;
  const busca = typeof sp.busca === "string" ? sp.busca : "";
  const status = typeof sp.status === "string" ? sp.status : "";
  const page = Math.max(1, Number(sp.page) || 1);
  const erroDaAcao = typeof sp.erro === "string" ? sp.erro : "";

  const qs = new URLSearchParams({ page: String(page), page_size: "25" });
  if (busca) qs.set("busca", busca);
  if (status) qs.set("status", status);

  const [resumo, processos, andamentos] = await Promise.all([
    seguro<Resumo>("/api/v1/juridico/resumo"),
    seguro<{ total: number; data: Processo[] }>(`/api/v1/juridico/processos?${qs}`),
    seguro<Andamento[]>("/api/v1/juridico/andamentos?limite=12"),
  ]);

  const nuncaSincronizou = resumo !== null && resumo.ultimo_sync === null;
  const sync = resumo?.ultimo_sync ?? null;
  const rodando = sync?.status === "em_andamento";
  const minutos = rodando ? minutosDesde(sync.iniciado_em) : null;
  // Uma carga leva ~2,5 min. Passou de 30, o worker provavelmente nao
  // esta de pe neste ambiente -- a API tambem deixa de bloquear o botao.
  const travado = rodando && (minutos ?? 0) >= 30;
  const totalPaginas = processos ? Math.max(1, Math.ceil(processos.total / 25)) : 1;

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Jurídico</h1>
          <p className="mt-1 text-sm text-slate-500">
            Processos e andamentos da Primor, espelhados do EasyJur. Somente
            leitura — o EasyJur segue sendo onde o escritório trabalha.
          </p>
        </div>
        <form action={sincronizar} className="text-right">
          <button
            type="submit"
            disabled={rodando && !travado}
            className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {rodando && !travado ? "Sincronizando…" : "Sincronizar agora"}
          </button>
          <p className="mt-1 text-xs text-slate-400">
            {rodando
              ? `Em andamento há ${minutos ?? 0} min — leva uns 3`
              : `Última: ${quando(sync?.executado_em ?? null)}`}
          </p>
        </form>
      </header>

      {rodando && !travado && <AutoRefresh segundos={15} />}

      {rodando && !travado && (
        <section className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
          <p className="font-semibold">Sincronizando com o EasyJur…</p>
          <p className="mt-1">
            Iniciada há {minutos ?? 0} min. O EasyJur demora para exportar os
            andamentos; esta página se atualiza sozinha a cada 15 segundos.
          </p>
        </section>
      )}

      {travado && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">A carga não terminou em {minutos} min.</p>
          <p className="mt-1">
            O normal são 3. Provavelmente o serviço que roda as cargas (worker)
            não está de pé neste ambiente. Dá para tentar de novo, mas sem o
            worker o resultado vai ser o mesmo.
          </p>
        </section>
      )}

      {((erroDaAcao && !rodando) || sync?.status === "erro") && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">A sincronização não rodou.</p>
          <p className="mt-1 font-mono text-xs">{erroDaAcao || sync?.erro}</p>
        </section>
      )}

      {resumo === null && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">Não consegui carregar os dados do Jurídico.</p>
          <p className="mt-1">
            É falha de requisição, não base vazia — pode ser o sistema fora do
            ar ou a sessão expirada.
          </p>
        </section>
      )}

      {nuncaSincronizou && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">Ainda não houve nenhuma sincronização.</p>
          <p className="mt-1">
            A rotina automática roda todo dia às 3h30. Para trazer os dados
            agora, use “Sincronizar agora” — leva uns 3 minutos e a tela
            acompanha. Os números abaixo ficam zerados até lá.
          </p>
        </section>
      )}

      {resumo?.ultimo_sync?.divergencia && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">A última carga veio incompleta.</p>
          <p className="mt-1">
            O EasyJur informou {resumo.ultimo_sync.total_declarado} processos e
            foram trazidos {resumo.ultimo_sync.processos}. Os dados abaixo podem
            estar parciais; sincronize de novo.
          </p>
        </section>
      )}

      <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          ["Processos", resumo?.total],
          ["Ativos", resumo?.por_status?.["Ativo"]],
          ["Andamentos", resumo?.andamentos],
          ["Ligados a uma obra", resumo?.com_obra],
        ].map(([rotulo, valor]) => (
          <div key={String(rotulo)} className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium text-slate-500">{rotulo}</p>
            <p className="mt-1 text-2xl font-bold tabular-nums">
              {valor === undefined || valor === null
                ? "—"
                : Number(valor).toLocaleString("pt-BR")}
            </p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold">Por situação</h2>
          <Distribuicao dados={resumo?.por_status ?? {}} />
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold">Por área</h2>
          <Distribuicao dados={resumo?.por_area ?? {}} />
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold">Por tribunal</h2>
          <Distribuicao dados={resumo?.por_tribunal ?? {}} />
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Campos preenchidos só em parte</h2>
        <p className="mt-1 text-xs text-slate-500">
          O escritório preenche estes campos em poucos processos. A distribuição
          vale apenas para os preenchidos — não para a carteira inteira.
        </p>
        <div className="mt-3 grid gap-4 md:grid-cols-4">
          {Object.entries(resumo?.campos_esparsos ?? {}).map(([campo, e]) => (
            <div key={campo}>
              <p className="text-sm font-medium">{ESPARSO_ROTULO[campo] ?? campo}</p>
              <p className="mb-2 text-xs text-amber-700">
                {e.preenchidos} de {e.total} preenchidos
                {e.total > 0 ? ` (${Math.round((100 * e.preenchidos) / e.total)}%)` : ""}
              </p>
              <Distribuicao dados={e.valores} />
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white">
        <h2 className="border-b border-slate-100 px-4 py-3 text-sm font-semibold">
          Últimos andamentos
        </h2>
        <ul className="divide-y divide-slate-100">
          {(andamentos ?? []).map((a) => (
            <li key={a.id} className="flex gap-4 px-4 py-2 text-sm">
              <span className="w-20 shrink-0 tabular-nums text-slate-500">
                {dataBr(a.data)}
              </span>
              <span className="w-52 shrink-0 font-mono text-xs text-slate-600">
                {a.numero_cnj}
              </span>
              <span className="min-w-0 flex-1 text-slate-700">{a.descricao ?? "—"}</span>
              <span className="shrink-0 text-xs text-slate-400">{a.status}</span>
            </li>
          ))}
          {(andamentos ?? []).length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-slate-400">
              Nenhum andamento carregado ainda.
            </li>
          )}
        </ul>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold">
            Processos{processos ? ` (${processos.total.toLocaleString("pt-BR")})` : ""}
          </h2>
          <form className="flex gap-2">
            <input
              name="busca"
              defaultValue={busca}
              placeholder="Número, cliente ou parte contrária"
              className="w-64 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            <select
              name="status"
              defaultValue={status}
              className="rounded-md border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="">Todas as situações</option>
              {Object.keys(resumo?.por_status ?? {}).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button
              type="submit"
              className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
            >
              Filtrar
            </button>
          </form>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-600">
              <tr>
                <th className="px-3 py-2">Processo</th>
                <th className="px-3 py-2">Situação</th>
                <th className="px-3 py-2">Área</th>
                <th className="px-3 py-2">Tribunal</th>
                <th className="px-3 py-2">Comarca</th>
                <th className="px-3 py-2">Cliente</th>
                <th className="px-3 py-2">Parte contrária</th>
                <th className="px-3 py-2">Obra</th>
              </tr>
            </thead>
            <tbody>
              {(processos?.data ?? []).map((p) => (
                <tr key={p.id} className="border-t border-slate-100">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                    {p.numero_cnj ?? "—"}
                  </td>
                  <td className="px-3 py-2">{p.status ?? "—"}</td>
                  <td className="px-3 py-2">{p.area ?? "—"}</td>
                  <td className="px-3 py-2">{p.tribunal ?? "—"}</td>
                  <td className="px-3 py-2">{p.comarca ?? "—"}</td>
                  <td className="max-w-48 truncate px-3 py-2">{p.cliente ?? "—"}</td>
                  <td className="max-w-48 truncate px-3 py-2">{p.contrario ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {p.codigo_obra ? (
                      <span
                        className={p.obra_id ? "" : "text-amber-700"}
                        title={
                          p.obra_id
                            ? undefined
                            : "O processo cita esta obra, mas ela ainda não está cadastrada no sistema"
                        }
                      >
                        {p.codigo_obra}
                        {p.obra_id ? "" : " · não cadastrada"}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
              {(processos?.data ?? []).length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-6 text-center text-slate-400">
                    {busca || status
                      ? "Nenhum processo com esse filtro."
                      : "Nenhum processo carregado ainda."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {totalPaginas > 1 && (
          <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2 text-sm text-slate-500">
            <span>
              Página {page} de {totalPaginas}
            </span>
            <span className="flex gap-3">
              {page > 1 && (
                <a
                  className="hover:underline"
                  href={`?${new URLSearchParams({ ...(busca && { busca }), ...(status && { status }), page: String(page - 1) })}`}
                >
                  ← Anterior
                </a>
              )}
              {page < totalPaginas && (
                <a
                  className="hover:underline"
                  href={`?${new URLSearchParams({ ...(busca && { busca }), ...(status && { status }), page: String(page + 1) })}`}
                >
                  Próxima →
                </a>
              )}
            </span>
          </div>
        )}
      </section>

      <ModuleStatusCard
        title="Escopo do módulo"
        backendPath="/api/v1/juridico"
        scope={[
          {
            label: "Contencioso do EasyJur (processos e andamentos).",
            status: "pronto",
            nota: "Carga automática diária às 3h30 e sob demanda pelo botão acima.",
          },
          {
            label: "Alertas de vencimento de contratos e locações.",
            status: "pronto",
            nota: "Rotina diária; vive no módulo Financeiro.",
          },
          {
            label: "Auditoria de acesso a documentos sensíveis (LGPD).",
            status: "pronto",
            nota: "Registro por funcionário e fonte a cada consulta.",
          },
          {
            label: "Relatórios de contratos judicializados.",
            status: "bloqueado",
            nota: "O EasyJur da Primor não tem contratos cadastrados — só o contencioso. Depende de o escritório passar a registrar os contratos lá.",
          },
          {
            label: "Reconhecimento facial (foto do ponto × fotos de obra).",
            status: "bloqueado",
            nota: "Biometria é dado pessoal sensível (LGPD art. 11) e exige consentimento específico dos funcionários e base legal definida. Precisa de decisão do cliente antes de qualquer desenvolvimento.",
          },
        ]}
      />
    </div>
  );
}
