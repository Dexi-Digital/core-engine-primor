/**
 * Jornada de admissao -- a tela que faltava.
 *
 * O RH tinha cadastro, dossie e alertas, mas nada que mostrasse uma
 * admissao EM ANDAMENTO. Saber o andamento exigia abrir funcionario
 * por funcionario e lembrar de cabeca o que o Dominio pede.
 *
 * Por isso o topo da pagina e a lista de TRAVADAS, nao o total: o que
 * a pessoa precisa ver ao abrir e o que depende dela hoje.
 */
import Link from "next/link";
import { revalidatePath } from "next/cache";

import { apiFetch, ApiError } from "@/lib/api";
import { SubNav } from "@/components/ui/sub-nav";
import { RH_SUBNAV } from "../subnav";

type JornadaItem = {
  jornada_id: number;
  employee_id: number;
  nome: string;
  obra: string | null;
  etapa: string;
  pendencias: string[];
  kit_gerado_em: string | null;
  kit_entregue_em: string | null;
  confirmado_em: string | null;
};

type Painel = {
  total: number;
  por_etapa: Record<string, number>;
  travadas: JornadaItem[];
  itens: JornadaItem[];
};

const ETAPA_LABEL: Record<string, string> = {
  rascunho: "Rascunho",
  dados_ok: "Dados completos",
  documentos_ok: "Documentos conferidos",
  kit_gerado: "Kit gerado",
  kit_entregue: "Kit entregue",
  concluida: "Concluída",
  cancelada: "Cancelada",
};

const ETAPA_BADGE: Record<string, string> = {
  rascunho: "bg-slate-200 text-slate-700",
  dados_ok: "bg-sky-100 text-sky-700",
  documentos_ok: "bg-indigo-100 text-indigo-700",
  kit_gerado: "bg-amber-100 text-amber-700",
  kit_entregue: "bg-violet-100 text-violet-700",
  concluida: "bg-emerald-100 text-emerald-700",
  cancelada: "bg-red-100 text-red-700",
};

// Ordem do fluxo -- usada na barra de progresso de cada linha.
const ETAPAS = [
  "rascunho",
  "dados_ok",
  "documentos_ok",
  "kit_gerado",
  "kit_entregue",
  "concluida",
];

export const dynamic = "force-dynamic";

function formatDateTime(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

/** Rotulo do botao de avanco -- cada etapa pede uma acao diferente. */
function proximaAcao(etapa: string): string | null {
  switch (etapa) {
    case "rascunho":
      return "Conferir dados";
    case "dados_ok":
      return "Conferir documentos";
    case "kit_gerado":
      return "Marcar como entregue";
    case "kit_entregue":
      return "Confirmar digitação";
    default:
      return null;
  }
}

async function fetchPainel(): Promise<Painel | null> {
  try {
    return await apiFetch<Painel>("/api/v1/dp-sesmt/admissao/painel");
  } catch {
    return null;
  }
}

async function fetchObras(painel: Painel | null): Promise<string[]> {
  if (!painel) return [];
  return [...new Set(painel.itens.map((i) => i.obra).filter(Boolean))] as string[];
}

async function abrirJornada(formData: FormData): Promise<void> {
  "use server";
  const cpf = String(formData.get("cpf") ?? "").trim();
  if (!cpf) return;
  await apiFetch("/api/v1/dp-sesmt/onboarding", {
    method: "POST",
    body: JSON.stringify({
      cpf,
      // O endpoint exige o funcionario ja cadastrado e le os dados do
      // banco; estes campos existem so para satisfazer o schema.
      nome_completo: "—",
      cargo: "—",
      data_admissao: "—",
    }),
  });
  revalidatePath("/rh/admissoes");
}

async function avancar(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("jornada_id");
  if (!id) return;
  const entregue_para =
    String(formData.get("entregue_para") ?? "").trim() || null;
  try {
    await apiFetch(`/api/v1/dp-sesmt/admissao/${id}/avancar`, {
      method: "POST",
      body: JSON.stringify({ entregue_para }),
    });
  } catch (e) {
    // 409 = requisitos em aberto. Nao e erro de sistema: e a jornada
    // funcionando. O painel ja mostra as pendencias, entao basta
    // recarregar em vez de estourar uma tela de erro.
    if (!(e instanceof ApiError) || e.status !== 409) throw e;
  }
  revalidatePath("/rh/admissoes");
}

async function gerarKit(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("jornada_id");
  if (!id) return;
  // POST gera e avanca a etapa; o CSV volta no corpo e e descartado
  // aqui de proposito -- o download fica no link GET, que nao muda
  // estado e pode ser clicado quantas vezes for preciso.
  await apiFetch(`/api/v1/dp-sesmt/admissao/${id}/kit`, { method: "POST" });
  revalidatePath("/rh/admissoes");
}

async function gerarKitLote(formData: FormData): Promise<void> {
  "use server";
  const obra = String(formData.get("obra") ?? "").trim();
  if (!obra) return;
  await apiFetch(
    `/api/v1/dp-sesmt/admissao/kit-lote?obra=${encodeURIComponent(obra)}`,
    { method: "POST" },
  );
  revalidatePath("/rh/admissoes");
}

export default async function AdmissoesPage() {
  const painel = await fetchPainel();
  const obras = await fetchObras(painel);

  if (!painel) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-800">
        Não foi possível carregar o painel de admissões. Verifique se a API
        está no ar.
      </div>
    );
  }

  const emAndamento = painel.itens.filter(
    (i) => i.etapa !== "concluida" && i.etapa !== "cancelada",
  );

  return (
    <div className="flex flex-col gap-8">
      <SubNav items={RH_SUBNAV} />
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Jornada de admissão</h1>
          <p className="mt-1 text-sm text-slate-600">
            O Motor Central conduz a admissão: verifica o que falta, gera o kit
            para a contabilidade — inclusive em lote por obra — e registra a
            entrega e a confirmação.
          </p>
        </div>
        <Link href="/rh" className="text-xs text-slate-500 hover:text-slate-800">
          ← voltar para RH
        </Link>
      </header>

      {/* Contadores por etapa */}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-6">
        {ETAPAS.map((e) => (
          <div
            key={e}
            className="rounded-lg border border-slate-200 bg-white p-3"
          >
            <div className="text-2xl font-semibold">
              {painel.por_etapa[e] ?? 0}
            </div>
            <div className="mt-1 text-xs text-slate-500">{ETAPA_LABEL[e]}</div>
          </div>
        ))}
      </section>

      {/* O que depende de alguem HOJE vem primeiro, de proposito. */}
      {painel.travadas.length > 0 && (
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-amber-800">
            Travadas — {painel.travadas.length} admissão(ões) aguardando
          </h2>
          <ul className="mt-3 space-y-2 text-sm">
            {painel.travadas.map((j) => (
              <li key={j.jornada_id} className="flex flex-wrap gap-2">
                <span className="font-medium">{j.nome}</span>
                <span className="text-amber-700">
                  {j.pendencias.join(" · ")}
                </span>
                <Link
                  href={`/rh/funcionarios/${j.employee_id}`}
                  className="text-xs text-amber-900 underline"
                >
                  completar cadastro
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Abrir jornada + kit em lote */}
      <section className="grid gap-4 md:grid-cols-2">
        <form
          action={abrirJornada}
          className="rounded-lg border border-slate-200 bg-white p-4"
        >
          <h2 className="text-sm font-semibold">Abrir admissão</h2>
          <p className="mt-1 text-xs text-slate-500">
            Informe o CPF de um funcionário já cadastrado. O sistema abre a
            jornada e lista o que falta.
          </p>
          <div className="mt-3 flex gap-2">
            <input
              name="cpf"
              placeholder="000.000.000-00"
              className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
            />
            <button
              type="submit"
              className="rounded bg-slate-900 px-3 py-1 text-sm text-white"
            >
              Abrir
            </button>
          </div>
        </form>

        <form
          action={gerarKitLote}
          className="rounded-lg border border-slate-200 bg-white p-4"
        >
          <h2 className="text-sm font-semibold">Kit em lote por obra</h2>
          <p className="mt-1 text-xs text-slate-500">
            Gera o kit de todas as admissões prontas da obra. Quem ainda tem
            pendência fica de fora e continua listado acima.
          </p>
          <div className="mt-3 flex gap-2">
            <select
              name="obra"
              className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
            >
              {obras.length === 0 && <option value="">— sem obras —</option>}
              {obras.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
            <button
              type="submit"
              className="rounded bg-slate-900 px-3 py-1 text-sm text-white"
            >
              Gerar
            </button>
          </div>
          {obras.length > 0 && (
            <p className="mt-2 text-xs">
              {obras.map((o) => (
                <a
                  key={o}
                  href={`/rh/admissoes/kit?obra=${encodeURIComponent(o)}`}
                  className="mr-3 text-slate-600 underline"
                >
                  baixar CSV — {o}
                </a>
              ))}
            </p>
          )}
        </form>
      </section>

      {/* Admissoes em andamento */}
      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Em andamento ({emAndamento.length} de {painel.total})
        </h2>
        <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2">Funcionário</th>
                <th className="px-3 py-2">Obra</th>
                <th className="px-3 py-2">Etapa</th>
                <th className="px-3 py-2">Pendências</th>
                <th className="px-3 py-2">Kit</th>
                <th className="px-3 py-2">Ação</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {emAndamento.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-slate-500">
                    Nenhuma admissão em andamento.
                  </td>
                </tr>
              )}
              {emAndamento.map((j) => {
                const acao = proximaAcao(j.etapa);
                const bloqueado = j.pendencias.length > 0;
                return (
                  <tr key={j.jornada_id}>
                    <td className="px-3 py-2">
                      <Link
                        href={`/rh/funcionarios/${j.employee_id}`}
                        className="hover:underline"
                      >
                        {j.nome}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-slate-600">{j.obra ?? "—"}</td>
                    <td className="px-3 py-2">
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${ETAPA_BADGE[j.etapa] ?? ""}`}
                      >
                        {ETAPA_LABEL[j.etapa] ?? j.etapa}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-600">
                      {j.pendencias.length === 0 ? (
                        <span className="text-emerald-700">—</span>
                      ) : (
                        j.pendencias.join(" · ")
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-600">
                      {j.kit_gerado_em ? (
                        <a
                          href={`/rh/admissoes/kit?jornada_id=${j.jornada_id}`}
                          className="underline"
                        >
                          {formatDateTime(j.kit_gerado_em)}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {j.etapa === "documentos_ok" ? (
                        <form action={gerarKit}>
                          <input
                            type="hidden"
                            name="jornada_id"
                            value={j.jornada_id}
                          />
                          <button
                            type="submit"
                            className="rounded bg-slate-900 px-2 py-1 text-xs text-white"
                          >
                            Gerar kit
                          </button>
                        </form>
                      ) : acao ? (
                        <form action={avancar} className="flex gap-1">
                          <input
                            type="hidden"
                            name="jornada_id"
                            value={j.jornada_id}
                          />
                          {j.etapa === "kit_gerado" && (
                            <input
                              name="entregue_para"
                              placeholder="para quem"
                              className="w-28 rounded border border-slate-300 px-1 text-xs"
                            />
                          )}
                          <button
                            type="submit"
                            disabled={bloqueado}
                            title={
                              bloqueado
                                ? "Resolva as pendências primeiro"
                                : undefined
                            }
                            className="rounded border border-slate-300 px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            {acao}
                          </button>
                        </form>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
