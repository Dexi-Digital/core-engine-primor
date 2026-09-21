/**
 * Caixa de notificações da plataforma.
 *
 * Substitui o e-mail dos boletins de licitação: em vez de disparar via
 * Resend três vezes ao dia, o despacho grava aqui e o sino da barra
 * lateral mostra o contador.
 *
 * O corpo vem como TEXTO da API, de propósito — o objeto de uma
 * licitação vem do PNCP, é texto de terceiro, e renderizá-lo como HTML
 * numa tela autenticada seria abrir XSS.
 */
import Link from "next/link";
import { revalidatePath } from "next/cache";

import { ApiError, apiFetch } from "@/lib/api";

type Notificacao = {
  id: number;
  categoria: string;
  titulo: string;
  corpo: string;
  link: string | null;
  lida_em: string | null;
  created_at: string;
};

type Caixa = { total_nao_lidas: number; data: Notificacao[] };

export const dynamic = "force-dynamic";

const CATEGORIA_ROTULO: Record<string, string> = {
  licitacoes: "Licitações",
  certidoes: "Certidões",
  dp: "RH / DP",
  frota: "Frota",
};

async function fetchCaixa(): Promise<Caixa | null> {
  try {
    return await apiFetch<Caixa>("/api/v1/notificacoes?limite=100");
  } catch {
    return null;
  }
}

async function marcarLida(formData: FormData): Promise<void> {
  "use server";
  const id = formData.get("id");
  if (!id) return;
  try {
    await apiFetch(`/api/v1/notificacoes/${id}/lida`, { method: "POST" });
  } catch (e) {
    // 404 = já foi lida noutra aba, ou não é desta pessoa. Recarregar
    // mostra o estado real; estourar a tela de erro não ajudaria.
    if (!(e instanceof ApiError) || e.status !== 404) throw e;
  }
  revalidatePath("/notificacoes");
  revalidatePath("/", "layout");
}

async function marcarTodasLidas(): Promise<void> {
  "use server";
  try {
    await apiFetch("/api/v1/notificacoes/lidas", { method: "POST" });
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
  }
  revalidatePath("/notificacoes");
  revalidatePath("/", "layout");
}

function quando(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
    dateStyle: "short",
    timeStyle: "short",
  });
}

export default async function NotificacoesPage() {
  const caixa = await fetchCaixa();

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Notificações</h1>
          <p className="mt-1 text-sm text-slate-500">
            Avisos do sistema. Os boletins de licitação chegam aqui — não
            por e-mail.
          </p>
        </div>
        {caixa && caixa.total_nao_lidas > 0 && (
          <form action={marcarTodasLidas}>
            <button
              type="submit"
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Marcar todas como lidas
            </button>
          </form>
        )}
      </header>

      {caixa === null && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">Não consegui carregar suas notificações.</p>
          <p className="mt-1">
            Isso é falha de requisição, não caixa vazia — pode ser a API fora
            do ar ou sessão expirada.
          </p>
        </section>
      )}

      {caixa && caixa.data.length === 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
          <p className="font-medium text-slate-700">Nenhuma notificação ainda.</p>
          <p className="mt-1">
            Os boletins de licitação são despachados pelo Celery beat às 7h,
            13h e 19h, e só geram aviso quando há oportunidade nova desde o
            último envio.
          </p>
        </section>
      )}

      <div className="space-y-2">
        {(caixa?.data ?? []).map((n) => {
          const naoLida = n.lida_em === null;
          return (
            <article
              key={n.id}
              className={`rounded-xl border p-4 ${
                naoLida
                  ? "border-emerald-200 bg-emerald-50/40"
                  : "border-slate-200 bg-white"
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-600">
                      {CATEGORIA_ROTULO[n.categoria] ?? n.categoria}
                    </span>
                    <span className="text-xs text-slate-400">
                      {quando(n.created_at)}
                    </span>
                  </div>
                  <h2
                    className={`mt-1 text-sm ${
                      naoLida ? "font-semibold text-slate-900" : "text-slate-700"
                    }`}
                  >
                    {n.titulo}
                  </h2>
                  {/* Texto puro: o conteúdo vem do PNCP. */}
                  <p className="mt-1 whitespace-pre-line text-sm text-slate-600">
                    {n.corpo}
                  </p>
                  {n.link && (
                    <Link
                      href={n.link}
                      className="mt-2 inline-block text-sm font-medium text-blue-700 hover:underline"
                    >
                      Ver oportunidades →
                    </Link>
                  )}
                </div>
                {naoLida && (
                  <form action={marcarLida}>
                    <input type="hidden" name="id" value={n.id} />
                    <button
                      type="submit"
                      className="whitespace-nowrap rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
                    >
                      Marcar como lida
                    </button>
                  </form>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
