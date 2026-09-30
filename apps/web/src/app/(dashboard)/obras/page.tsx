import Link from "next/link";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { apiFetch } from "@/lib/api";

import {
  STATUS_BADGE,
  STATUS_OBRA,
  campo,
  carregar,
  dataBr,
  detalheErro,
  local,
  type Obra,
} from "./shared";

export const dynamic = "force-dynamic";

// Sem paginacao na API: o teto cobre com folga o numero de canteiros.
const LIMITE = 500;

async function createObra(formData: FormData): Promise<void> {
  "use server";
  const codigo = campo(formData, "codigo");
  const nome = campo(formData, "nome");
  if (!codigo || !nome) {
    redirect(`/obras?${new URLSearchParams({ novo: "1", erro: "Código e nome são obrigatórios." })}`);
  }
  let criada: Obra;
  try {
    criada = await apiFetch<Obra>("/api/v1/obras", {
      method: "POST",
      body: JSON.stringify({
        codigo,
        nome,
        cliente: campo(formData, "cliente"),
        uf: campo(formData, "uf")?.toUpperCase() ?? null,
        cidade: campo(formData, "cidade"),
        status: campo(formData, "status") ?? "ativa",
        data_inicio: campo(formData, "data_inicio"),
        encerramento_previsto: campo(formData, "encerramento_previsto"),
      }),
    });
  } catch (e) {
    redirect(
      `/obras?${new URLSearchParams({ novo: "1", erro: `Não cadastrou: ${detalheErro(e)}` })}`,
    );
  }
  revalidatePath("/obras");
  redirect(`/obras/${criada.id}?${new URLSearchParams({ msg: "Obra cadastrada." })}`);
}

type SearchParams = Record<string, string | string[] | undefined>;

function texto(sp: SearchParams, k: string): string {
  const v = sp[k];
  return typeof v === "string" ? v.trim() : "";
}

export default async function ObrasPage(props: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await props.searchParams;
  const status = texto(sp, "status");
  const uf = texto(sp, "uf").toUpperCase();
  const busca = texto(sp, "busca");
  const msg = texto(sp, "msg");
  const erro = texto(sp, "erro");
  const novoAberto = texto(sp, "novo") === "1";
  const filtrando = Boolean(status || uf || busca);

  const qs = new URLSearchParams({ limit: String(LIMITE) });
  if (status) qs.set("obra_status", status);
  if (uf) qs.set("uf", uf);
  if (busca) qs.set("busca", busca);

  // Os KPIs e a lista de UFs olham o cadastro inteiro; a tabela, o filtro.
  const [todas, filtradas] = await Promise.all([
    carregar<Obra[]>(`/api/v1/obras?limit=${LIMITE}`),
    filtrando ? carregar<Obra[]>(`/api/v1/obras?${qs}`) : null,
  ]);
  const lista = filtradas ?? todas;

  const contagem = (s: string) =>
    todas.ok ? todas.data.filter((o) => o.status === s).length : null;
  const ufs = todas.ok
    ? [...new Set(todas.data.map((o) => o.uf).filter((u): u is string => !!u))].sort()
    : [];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Obras</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Cadastro de canteiros. Abra uma obra para ver os documentos
            (ART, alvará, PCMAT, CIPA, diário de obra) e o que está ligado a
            ela em contratos, notas fiscais, ponto, jurídico, frota e equipe.
          </p>
        </div>
      </header>

      {msg && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
          {msg}
        </div>
      )}
      {erro && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">
          {erro}
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {(
          [
            ["Total", todas.ok ? todas.data.length : null, "border-slate-200"],
            ["Ativas", contagem("ativa"), "border-emerald-200"],
            ["Suspensas", contagem("suspensa"), "border-amber-300"],
            ["Encerradas", contagem("encerrada"), "border-slate-300"],
          ] as Array<[string, number | null, string]>
        ).map(([label, value, border]) => (
          <div key={label} className={`rounded-xl border ${border} bg-white p-4`}>
            <p className="text-xs font-medium text-slate-500">{label}</p>
            <p className="mt-1 text-2xl font-bold text-slate-900">{value ?? "—"}</p>
          </div>
        ))}
      </section>

      <details
        open={novoAberto}
        className="rounded-lg border border-slate-200 bg-white p-4"
      >
        <summary className="cursor-pointer text-sm font-semibold text-slate-800">
          + Nova obra
        </summary>
        <form action={createObra} className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Código *</span>
            <input name="codigo" required maxLength={32} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
          </label>
          <label className="flex flex-col text-xs md:col-span-2">
            <span className="mb-1 font-medium text-slate-700">Nome *</span>
            <input name="nome" required maxLength={255} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
          </label>
          <label className="flex flex-col text-xs md:col-span-2">
            <span className="mb-1 font-medium text-slate-700">Cliente</span>
            <input name="cliente" maxLength={255} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">UF</span>
            <input name="uf" maxLength={2} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm uppercase" />
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Cidade</span>
            <input name="cidade" maxLength={128} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Status</span>
            <select name="status" defaultValue="ativa" className="rounded-md border border-slate-300 px-2 py-1.5 text-sm">
              {STATUS_OBRA.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Data início</span>
            <input type="date" name="data_inicio" className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
          </label>
          <label className="flex flex-col text-xs">
            <span className="mb-1 font-medium text-slate-700">Encerramento previsto</span>
            <input type="date" name="encerramento_previsto" className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
          </label>
          <div className="md:col-span-3">
            <button type="submit" className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">
              Cadastrar obra
            </button>
          </div>
        </form>
      </details>

      <section className="rounded-lg border border-slate-200 bg-white">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold">
            Obras cadastradas{" "}
            {lista.ok && (
              <span className="text-xs font-normal text-slate-500">
                ({lista.data.length}
                {filtrando && todas.ok ? ` de ${todas.data.length}` : ""})
              </span>
            )}
          </h2>
          <form method="get" className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col text-xs">
              <span className="mb-1 text-slate-500">Busca</span>
              <input
                name="busca"
                defaultValue={busca}
                placeholder="código ou nome"
                className="w-48 rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
            </label>
            <label className="flex flex-col text-xs">
              <span className="mb-1 text-slate-500">Status</span>
              <select name="status" defaultValue={status} className="rounded-md border border-slate-300 px-2 py-1 text-sm">
                <option value="">todos</option>
                {STATUS_OBRA.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col text-xs">
              <span className="mb-1 text-slate-500">UF</span>
              <select name="uf" defaultValue={uf} className="rounded-md border border-slate-300 px-2 py-1 text-sm">
                <option value="">todas</option>
                {ufs.map((u) => (
                  <option key={u} value={u}>{u}</option>
                ))}
              </select>
            </label>
            <button type="submit" className="rounded-md border border-slate-300 bg-white px-3 py-1 text-sm hover:bg-slate-50">
              Filtrar
            </button>
            {filtrando && (
              <Link href="/obras" className="px-1 py-1 text-xs text-slate-500 hover:underline">
                limpar
              </Link>
            )}
          </form>
        </div>

        {!lista.ok ? (
          <div className="m-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">
            Não foi possível carregar as obras ({lista.erro}). Isto não
            significa que o cadastro esteja vazio — tente recarregar a página.
          </div>
        ) : lista.data.length === 0 ? (
          <p className="px-4 py-8 text-sm text-slate-500">
            {filtrando
              ? "Nenhuma obra corresponde aos filtros."
              : "Nenhuma obra cadastrada. Use “+ Nova obra” acima para começar."}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-4 py-2">Código</th>
                  <th className="px-4 py-2">Nome / Cliente</th>
                  <th className="px-4 py-2">Local</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Início</th>
                  <th className="px-4 py-2">Prev. encerr.</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {lista.data.map((o) => (
                  <tr key={o.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-4 py-2 font-mono text-xs">
                      <Link href={`/obras/${o.id}`} className="hover:underline">
                        {o.codigo}
                      </Link>
                    </td>
                    <td className="px-4 py-2">
                      <Link href={`/obras/${o.id}`} className="font-medium hover:underline">
                        {o.nome}
                      </Link>
                      {o.cliente && <div className="text-xs text-slate-500">{o.cliente}</div>}
                    </td>
                    <td className="px-4 py-2 text-slate-700">{local(o)}</td>
                    <td className="px-4 py-2">
                      <span className={`rounded px-2 py-0.5 text-xs ${STATUS_BADGE[o.status] ?? ""}`}>
                        {o.status}
                      </span>
                    </td>
                    <td className="px-4 py-2">{dataBr(o.data_inicio)}</td>
                    <td className="px-4 py-2">{dataBr(o.encerramento_previsto)}</td>
                    <td className="px-4 py-2 text-right">
                      <Link href={`/obras/${o.id}`} className="text-xs text-slate-600 hover:underline">
                        abrir →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
