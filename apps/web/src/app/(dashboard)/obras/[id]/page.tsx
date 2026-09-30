/**
 * Obra — a ficha do canteiro.
 *
 * Junta o cadastro (editavel), os documentos obrigatorios do
 * diagnostico e o que ja esta ligado a obra nos outros modulos:
 * contratos, notas fiscais, locais de ponto, processos, frota e equipe.
 * Cada secao carrega sozinha e diz se esta vazia ou se a API falhou --
 * "nenhum" e "nao consegui ver" nao podem parecer a mesma coisa.
 */
import Link from "next/link";
import { revalidatePath } from "next/cache";
import { notFound, redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ApiError, apiFetch } from "@/lib/api";

import {
  STATUS_BADGE,
  STATUS_OBRA,
  campo,
  carregar,
  dataBr,
  detalheErro,
  local,
  type Carga,
  type Obra,
} from "../shared";

export const dynamic = "force-dynamic";

type ObraDocumento = {
  id: number;
  obra_id: number;
  tipo: string;
  numero: string | null;
  emissao: string | null;
  validade: string | null;
  orgao_emissor: string | null;
  anexo_path: string | null;
  observacoes: string | null;
  source: string;
};

type Contrato = {
  id: number;
  titulo: string;
  contraparte_nome: string;
  tipo: string;
  valor: string | null;
  data_inicio: string;
  data_fim: string | null;
  status: string;
  vencimento_status: string | null;
};

type NotaFiscal = {
  id: number;
  tipo: string;
  numero: string | null;
  serie: string | null;
  emitente_nome: string | null;
  valor_total: string | null;
  data_emissao: string | null;
  status_envio: string;
};

type LocalPonto = {
  id: number;
  nome: string;
  ativo: boolean;
  codigo_obra: string | null;
};

type Processo = {
  id: number;
  numero_cnj: string | null;
  status: string | null;
  area: string | null;
  titulo: string | null;
  contrario: string | null;
  fase_atual: string | null;
};

type Veiculo = {
  id: number;
  placa: string;
  marca: string | null;
  modelo: string | null;
  tipo: string | null;
  status: string;
  obra: string | null;
};

type Funcionario = {
  id: number;
  nome_completo: string;
  cargo: string;
  status: string;
  obra: string | null;
};

// Tipos de ObraDocumento (apps/api/app/modules/obras/models.py).
const DOC_LABEL: Record<string, string> = {
  ART: "ART (Anotação de Responsabilidade Técnica)",
  RRT: "RRT (Registro de Responsabilidade Técnica)",
  ALVARA: "Alvará de construção",
  ARTEX: "ART de execução (ARTEx)",
  PCMAT: "PCMAT (NR-18)",
  PGR_OBRA: "PGR da obra (NR-1)",
  CIPA_OBRA: "CIPA da obra",
  DIARIO_OBRA: "Diário de obra",
  MEDICAO: "Medição",
  CHECKLIST_ALOJAMENTO: "Checklist de alojamento",
  CHECKLIST_VIVENCIA: "Checklist de áreas de vivência",
  LAUDO_BANHEIRO_QUIMICO: "Laudo de banheiro químico",
  RNC: "Relatório de Não Conformidade (RNC)",
  RIA: "Relatório de Investigação de Acidentes (RIA)",
  OUTRO: "Outro",
};

// Mesmo checklist do diagnostico documental (CHECKLIST_OBRA) e mesma
// janela de "vencendo" (VENCENDO_THRESHOLD_DIAS = 30).
const OBRIGATORIOS = ["ART", "ALVARA", "PCMAT", "CIPA_OBRA", "DIARIO_OBRA"];
const VENCENDO_DIAS = 30;

type Situacao = "presente" | "vencendo" | "vencido" | "ausente";

const SITUACAO: Record<Situacao, { rotulo: string; classe: string }> = {
  presente: { rotulo: "presente", classe: "bg-emerald-100 text-emerald-800" },
  vencendo: { rotulo: "vencendo", classe: "bg-amber-100 text-amber-800" },
  vencido: { rotulo: "vencido", classe: "bg-red-100 text-red-800" },
  ausente: { rotulo: "ausente", classe: "bg-slate-100 text-slate-600" },
};

function hojeIso(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Sao_Paulo" });
}

function diasAte(iso: string, hoje: string): number {
  const d = (s: string) => Date.parse(`${s.slice(0, 10)}T00:00:00Z`);
  return Math.round((d(iso) - d(hoje)) / 86_400_000);
}

/** Situacao de um documento isolado pela validade. */
function situacaoDoc(doc: ObraDocumento, hoje: string): Situacao {
  if (!doc.validade) return "presente";
  const dias = diasAte(doc.validade, hoje);
  if (dias < 0) return "vencido";
  if (dias <= VENCENDO_DIAS) return "vencendo";
  return "presente";
}

/** Situacao de um tipo obrigatorio: vale o melhor documento daquele tipo. */
function situacaoTipo(
  docs: ObraDocumento[],
  tipo: string,
  hoje: string,
): { situacao: Situacao; doc: ObraDocumento | null } {
  const doTipo = docs.filter((d) => d.tipo === tipo);
  if (doTipo.length === 0) return { situacao: "ausente", doc: null };
  const semValidade = doTipo.find((d) => !d.validade);
  const melhor =
    semValidade ??
    doTipo.reduce((a, b) => ((a.validade ?? "") >= (b.validade ?? "") ? a : b));
  return { situacao: situacaoDoc(melhor, hoje), doc: melhor };
}

function moeda(v: string | null): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

/** Volta para a propria pagina com uma mensagem (padrao das fichas). */
function volta(id: string, msg?: string, erro?: string): never {
  const q = new URLSearchParams();
  if (msg) q.set("msg", msg);
  if (erro) q.set("erro", erro);
  revalidatePath(`/obras/${id}`);
  redirect(`/obras/${id}${q.size ? `?${q}` : ""}`);
}

async function salvarObra(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  const codigo = campo(formData, "codigo");
  const nome = campo(formData, "nome");
  if (!codigo || !nome) volta(id, undefined, "Código e nome são obrigatórios.");
  try {
    await apiFetch(`/api/v1/obras/${id}`, {
      method: "PUT",
      body: JSON.stringify({
        codigo,
        nome,
        cliente: campo(formData, "cliente"),
        uf: campo(formData, "uf")?.toUpperCase() ?? null,
        cidade: campo(formData, "cidade"),
        status: campo(formData, "status") ?? "ativa",
        data_inicio: campo(formData, "data_inicio"),
        encerramento_previsto: campo(formData, "encerramento_previsto"),
        data_encerramento: campo(formData, "data_encerramento"),
        observacoes: campo(formData, "observacoes"),
      }),
    });
  } catch (e) {
    volta(id, undefined, `Não salvou: ${detalheErro(e)}`);
  }
  revalidatePath("/obras");
  volta(id, "Cadastro da obra salvo.");
}

async function excluirObra(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  const codigo = String(formData.get("codigo") ?? "");
  const digitado = String(formData.get("confirmacao") ?? "").trim();
  if (!id) return;
  if (!codigo || digitado !== codigo) {
    volta(
      id,
      undefined,
      `Exclusão cancelada: digite exatamente o código "${codigo}" para confirmar.`,
    );
  }
  try {
    await apiFetch(`/api/v1/obras/${id}`, { method: "DELETE" });
  } catch (e) {
    volta(id, undefined, `Não excluiu: ${detalheErro(e)}`);
  }
  revalidatePath("/obras");
  redirect(`/obras?${new URLSearchParams({ msg: `Obra ${codigo} excluída.` })}`);
}

async function adicionarDocumento(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  const tipo = campo(formData, "tipo");
  if (!id) return;
  if (!tipo) volta(id, undefined, "Escolha o tipo do documento.");
  try {
    await apiFetch(`/api/v1/obras/${id}/documentos`, {
      method: "POST",
      body: JSON.stringify({
        tipo,
        numero: campo(formData, "numero"),
        emissao: campo(formData, "emissao"),
        validade: campo(formData, "validade"),
        orgao_emissor: campo(formData, "orgao_emissor"),
        observacoes: campo(formData, "observacoes"),
      }),
    });
  } catch (e) {
    volta(id, undefined, `Documento não registrado: ${detalheErro(e)}`);
  }
  volta(id, `Documento ${DOC_LABEL[tipo ?? ""] ?? tipo} registrado.`);
}

/**
 * Frota e DP guardam a obra como texto livre (sem FK) e filtram por
 * igualdade exata. Nao sabemos se o texto e o codigo ou o nome, entao
 * consultamos os dois e juntamos sem repetir.
 */
async function porTextoDeObra<T extends { id: number }>(
  valores: string[],
  montar: (valor: string) => string,
  extrair: (resposta: { items: T[] }) => T[],
): Promise<Carga<T[]>> {
  const cargas = await Promise.all(
    [...new Set(valores.filter(Boolean))].map((v) =>
      carregar<{ items: T[] }>(montar(v)),
    ),
  );
  const falha = cargas.find((c) => !c.ok);
  if (falha && !falha.ok) return falha;
  const vistos = new Map<number, T>();
  for (const c of cargas) {
    if (c.ok) for (const item of extrair(c.data)) vistos.set(item.id, item);
  }
  return { ok: true, data: [...vistos.values()] };
}

async function buscarObra(id: number): Promise<Carga<Obra> | null> {
  try {
    return { ok: true, data: await apiFetch<Obra>(`/api/v1/obras/${id}`) };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    return { ok: false, erro: detalheErro(e) };
  }
}

const inputCls = "rounded border border-slate-300 px-2 py-1 text-sm";

function Campo({
  name, label, value, type = "text", maxLength, required, className = "",
}: {
  name: string; label: string; value: string | null; type?: string;
  maxLength?: number; required?: boolean; className?: string;
}) {
  return (
    <label className={`flex flex-col gap-1 text-xs ${className}`}>
      <span className="font-medium uppercase tracking-wide text-slate-500">
        {label}{required ? " *" : ""}
      </span>
      <input
        name={name}
        type={type}
        defaultValue={value ?? ""}
        maxLength={maxLength}
        required={required}
        className={inputCls}
      />
    </label>
  );
}

function Field({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className={`text-sm text-slate-700 ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

/** Casca de secao com os tres estados: erro, vazio e conteudo. */
function Secao<T>({
  titulo, carga, vazio, legenda, acao, children,
}: {
  titulo: string;
  carga: Carga<T[]>;
  vazio: string;
  legenda?: ReactNode;
  acao?: ReactNode;
  children: (itens: T[]) => ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <h2 className="text-sm font-semibold">
          {titulo}
          {carga.ok && (
            <span className="ml-1 text-xs font-normal text-slate-500">({carga.data.length})</span>
          )}
        </h2>
        {acao}
      </header>
      {legenda && <p className="px-4 pt-3 text-xs text-slate-500">{legenda}</p>}
      {!carga.ok ? (
        <p className="m-4 rounded bg-red-50 px-3 py-2 text-sm text-red-800">
          Não foi possível carregar ({carga.erro}).
        </p>
      ) : carga.data.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500">{vazio}</p>
      ) : (
        <div className="overflow-x-auto">{children(carga.data)}</div>
      )}
    </section>
  );
}

const thCls = "px-4 py-2 text-left";
const tdCls = "px-4 py-2";

export default async function ObraPage(props: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id: idStr } = await props.params;
  const id = Number(idStr);
  if (!Number.isInteger(id) || id <= 0) notFound();
  const sp = await props.searchParams;
  const msg = typeof sp.msg === "string" ? sp.msg : "";
  const erro = typeof sp.erro === "string" ? sp.erro : "";

  const [obraCarga, documentos, contratos, notas, locais, processos] =
    await Promise.all([
      buscarObra(id),
      carregar<ObraDocumento[]>(`/api/v1/obras/${id}/documentos`),
      carregar<Contrato[]>(`/api/v1/financeiro/contratos?obra_id=${id}`),
      carregar<NotaFiscal[]>(`/api/v1/fiscal/documentos?obra_id=${id}&limit=200`),
      carregar<LocalPonto[]>(`/api/v1/ponto/locais?obra_id=${id}`),
      carregar<{ total: number; data: Processo[] }>(
        `/api/v1/juridico/processos?obra_id=${id}&page_size=200`,
      ),
    ]);

  if (obraCarga === null) notFound();
  if (!obraCarga.ok) {
    return (
      <div className="flex flex-col gap-6">
        <Link href="/obras" className="text-xs text-slate-500 hover:underline">← Obras</Link>
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-900">
          Não foi possível carregar a obra ({obraCarga.erro}). Tente recarregar a página.
        </div>
      </div>
    );
  }
  const obra = obraCarga.data;

  const [veiculos, equipe] = await Promise.all([
    porTextoDeObra<Veiculo>(
      [obra.codigo, obra.nome],
      (v) => `/api/v1/manutencao-frota/veiculos?${new URLSearchParams({ obra: v, page_size: "200" })}`,
      (r) => r.items,
    ),
    porTextoDeObra<Funcionario>(
      [obra.codigo, obra.nome],
      (v) => `/api/v1/dp-sesmt/employees?${new URLSearchParams({ obra: v, limit: "200" })}`,
      (r) => r.items,
    ),
  ]);

  const hoje = hojeIso();
  const processosCarga: Carga<Processo[]> = processos.ok
    ? { ok: true, data: processos.data.data }
    : processos;
  const processosTotal = processos.ok ? processos.data.total : 0;
  const legendaTexto = (
    <>
      Frota e DP guardam a obra como texto livre, sem vínculo com o cadastro.
      Aqui aparecem os registros cujo campo “obra” é exatamente o código{" "}
      <span className="font-mono">{obra.codigo}</span> ou o nome “{obra.nome}”.
    </>
  );

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/obras" className="text-xs text-slate-500 hover:underline">
            ← Obras
          </Link>
          <h1 className="text-2xl font-semibold">
            <span className="font-mono text-lg text-slate-500">{obra.codigo}</span>{" "}
            {obra.nome}
            <span
              className={`ml-3 inline-flex items-center rounded-full px-2 py-0.5 align-middle text-xs font-medium ${
                STATUS_BADGE[obra.status] ?? "bg-slate-100 text-slate-700"
              }`}
            >
              {obra.status}
            </span>
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            {obra.cliente ?? "cliente não informado"} · {local(obra)}
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

      <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <h2 className="mb-3 text-sm font-semibold">Cadastro</h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 md:grid-cols-4">
          <Field label="Código" value={obra.codigo} mono />
          <Field label="Cliente" value={obra.cliente ?? "—"} />
          <Field label="Cidade / UF" value={local(obra)} />
          <Field label="Status" value={obra.status} />
          <Field label="Início" value={dataBr(obra.data_inicio)} />
          <Field label="Encerramento previsto" value={dataBr(obra.encerramento_previsto)} />
          <Field label="Encerrada em" value={dataBr(obra.data_encerramento)} />
        </dl>
        <dl className="mt-3">
          <dt className="text-xs uppercase tracking-wide text-slate-400">Observações</dt>
          <dd className="whitespace-pre-line text-sm text-slate-700">{obra.observacoes || "—"}</dd>
        </dl>

        <details className="mt-4 border-t border-slate-100 pt-3">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">
            Editar cadastro
          </summary>
          <form action={salvarObra} className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-4">
            <input type="hidden" name="id" value={obra.id} />
            <Campo name="codigo" label="Código" value={obra.codigo} maxLength={32} required />
            <Campo name="nome" label="Nome" value={obra.nome} maxLength={255} required className="md:col-span-3" />
            <Campo name="cliente" label="Cliente" value={obra.cliente} maxLength={255} className="md:col-span-2" />
            <Campo name="cidade" label="Cidade" value={obra.cidade} maxLength={128} />
            <Campo name="uf" label="UF" value={obra.uf} maxLength={2} />
            <label className="flex flex-col gap-1 text-xs">
              <span className="font-medium uppercase tracking-wide text-slate-500">Status</span>
              <select name="status" defaultValue={obra.status} className={inputCls}>
                {STATUS_OBRA.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <Campo name="data_inicio" label="Início" value={obra.data_inicio} type="date" />
            <Campo name="encerramento_previsto" label="Encerramento previsto" value={obra.encerramento_previsto} type="date" />
            <Campo name="data_encerramento" label="Encerrada em" value={obra.data_encerramento} type="date" />
            <label className="flex flex-col gap-1 text-xs md:col-span-4">
              <span className="font-medium uppercase tracking-wide text-slate-500">Observações</span>
              <textarea name="observacoes" rows={3} defaultValue={obra.observacoes ?? ""} className={inputCls} />
            </label>
            <div className="md:col-span-4">
              <button type="submit" className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">
                Salvar alterações
              </button>
            </div>
          </form>
        </details>
      </section>

      {/* Documentos */}
      <section className="rounded-lg border border-slate-200 bg-white">
        <header className="border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold">Documentos obrigatórios</h2>
          <p className="mt-1 text-xs text-slate-500">
            Mesmo checklist do diagnóstico documental. “Vencendo” = validade nos
            próximos {VENCENDO_DIAS} dias; documento sem validade conta como presente.
          </p>
        </header>
        {!documentos.ok ? (
          <p className="m-4 rounded bg-red-50 px-3 py-2 text-sm text-red-800">
            Não foi possível carregar os documentos ({documentos.erro}).
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-2 p-4 sm:grid-cols-2 lg:grid-cols-5">
            {OBRIGATORIOS.map((tipo) => {
              const { situacao, doc } = situacaoTipo(documentos.data, tipo, hoje);
              return (
                <li key={tipo} className="rounded border border-slate-200 p-3">
                  <div className="text-xs font-medium text-slate-700">{DOC_LABEL[tipo]}</div>
                  <span className={`mt-2 inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${SITUACAO[situacao].classe}`}>
                    {SITUACAO[situacao].rotulo}
                  </span>
                  <div className="mt-1 text-xs text-slate-500">
                    {doc ? (doc.validade ? `validade ${dataBr(doc.validade)}` : "sem validade") : "nenhum registro"}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <Secao
        titulo="Todos os documentos"
        carga={documentos}
        vazio="Nenhum documento registrado para esta obra."
      >
        {(docs) => (
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className={thCls}>Tipo</th>
                <th className={thCls}>Número</th>
                <th className={thCls}>Emissão</th>
                <th className={thCls}>Validade</th>
                <th className={thCls}>Órgão emissor</th>
                <th className={thCls}>Obs</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => {
                const s = situacaoDoc(d, hoje);
                return (
                  <tr key={d.id} className="border-t border-slate-100 text-slate-700">
                    <td className={`${tdCls} font-medium`}>{DOC_LABEL[d.tipo] ?? d.tipo}</td>
                    <td className={`${tdCls} text-xs`}>{d.numero ?? "—"}</td>
                    <td className={`${tdCls} text-xs`}>{dataBr(d.emissao)}</td>
                    <td className={`${tdCls} text-xs`}>
                      {dataBr(d.validade)}
                      {d.validade && s !== "presente" && (
                        <span className={`ml-2 rounded-full px-2 py-0.5 ${SITUACAO[s].classe}`}>
                          {SITUACAO[s].rotulo}
                        </span>
                      )}
                    </td>
                    <td className={`${tdCls} text-xs`}>{d.orgao_emissor ?? "—"}</td>
                    <td className={`${tdCls} text-xs text-slate-500`}>{d.observacoes ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Secao>

      <details className="rounded-lg border border-slate-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-semibold text-slate-800">
          + Registrar documento
        </summary>
        <form action={adicionarDocumento} className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          <input type="hidden" name="id" value={obra.id} />
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium uppercase tracking-wide text-slate-500">Tipo *</span>
            <select name="tipo" required defaultValue="" className={inputCls}>
              <option value="" disabled>— selecione —</option>
              {Object.entries(DOC_LABEL).map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </label>
          <Campo name="numero" label="Número" value={null} maxLength={128} />
          <Campo name="orgao_emissor" label="Órgão emissor" value={null} maxLength={255} />
          <Campo name="emissao" label="Emissão" value={null} type="date" />
          <Campo name="validade" label="Validade" value={null} type="date" />
          <Campo name="observacoes" label="Observações" value={null} />
          <div className="md:col-span-3">
            <button type="submit" className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">
              Registrar documento
            </button>
          </div>
        </form>
      </details>

      <Secao
        titulo="Contratos"
        carga={contratos}
        vazio="Nenhum contrato vinculado a esta obra."
        acao={<Link href="/financeiro/contratos" className="text-xs text-slate-500 hover:underline">ver contratos →</Link>}
      >
        {(itens) => (
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className={thCls}>Título</th>
                <th className={thCls}>Contraparte</th>
                <th className={thCls}>Valor</th>
                <th className={thCls}>Vigência</th>
                <th className={thCls}>Status</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((c) => (
                <tr key={c.id} className="border-t border-slate-100 text-slate-700">
                  <td className={`${tdCls} font-medium`}>{c.titulo}</td>
                  <td className={`${tdCls} text-xs`}>{c.contraparte_nome}</td>
                  <td className={`${tdCls} text-xs`}>{moeda(c.valor)}</td>
                  <td className={`${tdCls} text-xs`}>{dataBr(c.data_inicio)} – {dataBr(c.data_fim)}</td>
                  <td className={`${tdCls} text-xs`}>
                    {c.status}
                    {c.vencimento_status && c.status !== "encerrado" ? ` · ${c.vencimento_status}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Secao>

      <Secao
        titulo="Notas fiscais"
        carga={notas}
        vazio="Nenhuma nota fiscal vinculada a esta obra."
      >
        {(itens) => (
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className={thCls}>Nota</th>
                <th className={thCls}>Emitente</th>
                <th className={thCls}>Emissão</th>
                <th className={thCls}>Valor</th>
                <th className={thCls}>Envio</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((n) => (
                <tr key={n.id} className="border-t border-slate-100 text-slate-700">
                  <td className={tdCls}>
                    <Link href={`/fiscal/documentos/${n.id}`} className="font-medium hover:underline">
                      {n.tipo.toUpperCase()} {n.numero ?? "s/n"}
                      {n.serie ? `-${n.serie}` : ""}
                    </Link>
                  </td>
                  <td className={`${tdCls} text-xs`}>{n.emitente_nome ?? "—"}</td>
                  <td className={`${tdCls} text-xs`}>{dataBr(n.data_emissao)}</td>
                  <td className={`${tdCls} text-xs`}>{moeda(n.valor_total)}</td>
                  <td className={`${tdCls} text-xs`}>{n.status_envio}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Secao>

      <Secao
        titulo="Ponto — locais de trabalho"
        carga={locais}
        vazio="Nenhum local de trabalho do ponto (Tangerino) ligado a esta obra."
        acao={<Link href="/rh/ponto" className="text-xs text-slate-500 hover:underline">ver ponto →</Link>}
      >
        {(itens) => (
          <ul className="divide-y divide-slate-100 text-sm">
            {itens.map((l) => (
              <li key={l.id} className="flex items-center justify-between px-4 py-2">
                <span>{l.nome}</span>
                <span className="text-xs text-slate-500">{l.ativo ? "ativo" : "inativo"}</span>
              </li>
            ))}
          </ul>
        )}
      </Secao>

      <Secao
        titulo="Jurídico — processos"
        carga={processosCarga}
        vazio="Nenhum processo vinculado a esta obra."
        legenda={
          processos.ok && processosTotal > processos.data.data.length
            ? `Mostrando ${processos.data.data.length} de ${processosTotal}.`
            : undefined
        }
        acao={<Link href="/juridico" className="text-xs text-slate-500 hover:underline">ver jurídico →</Link>}
      >
        {(itens) => (
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className={thCls}>Processo</th>
                <th className={thCls}>Parte contrária</th>
                <th className={thCls}>Área</th>
                <th className={thCls}>Fase</th>
                <th className={thCls}>Status</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((p) => (
                <tr key={p.id} className="border-t border-slate-100 text-slate-700">
                  <td className={`${tdCls} font-mono text-xs`}>
                    {p.numero_cnj ? (
                      <Link href={`/juridico?${new URLSearchParams({ busca: p.numero_cnj })}`} className="hover:underline">
                        {p.numero_cnj}
                      </Link>
                    ) : (
                      p.titulo ?? "—"
                    )}
                  </td>
                  <td className={`${tdCls} text-xs`}>{p.contrario ?? "—"}</td>
                  <td className={`${tdCls} text-xs`}>{p.area ?? "—"}</td>
                  <td className={`${tdCls} text-xs`}>{p.fase_atual ?? "—"}</td>
                  <td className={`${tdCls} text-xs`}>{p.status ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Secao>

      <Secao
        titulo="Frota — veículos e equipamentos"
        carga={veiculos}
        vazio="Nenhum veículo com esta obra no cadastro da frota."
        legenda={legendaTexto}
      >
        {(itens) => (
          <table className="w-full min-w-[560px] text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className={thCls}>Placa</th>
                <th className={thCls}>Veículo</th>
                <th className={thCls}>Tipo</th>
                <th className={thCls}>Status</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((v) => (
                <tr key={v.id} className="border-t border-slate-100 text-slate-700">
                  <td className={`${tdCls} font-mono text-xs`}>
                    <Link href={`/manutencao/veiculos/${v.id}`} className="hover:underline">{v.placa}</Link>
                  </td>
                  <td className={`${tdCls} text-xs`}>{[v.marca, v.modelo].filter(Boolean).join(" ") || "—"}</td>
                  <td className={`${tdCls} text-xs`}>{v.tipo ?? "—"}</td>
                  <td className={`${tdCls} text-xs`}>{v.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Secao>

      <Secao
        titulo="Equipe (DP)"
        carga={equipe}
        vazio="Nenhum funcionário com esta obra no cadastro do DP."
        legenda={legendaTexto}
      >
        {(itens) => (
          <table className="w-full min-w-[560px] text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className={thCls}>Nome</th>
                <th className={thCls}>Cargo</th>
                <th className={thCls}>Status</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((f) => (
                <tr key={f.id} className="border-t border-slate-100 text-slate-700">
                  <td className={tdCls}>
                    <Link href={`/rh/funcionarios/${f.id}`} className="font-medium hover:underline">{f.nome_completo}</Link>
                  </td>
                  <td className={`${tdCls} text-xs`}>{f.cargo}</td>
                  <td className={`${tdCls} text-xs`}>{f.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Secao>

      {/* Exclusao: confirmacao explicita digitando o codigo */}
      <details className="rounded-lg border border-red-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-semibold text-red-700">
          Excluir obra
        </summary>
        <div className="mt-3 text-sm text-slate-700">
          <p>
            A exclusão é definitiva e apaga também{" "}
            <strong>
              {documentos.ok ? `${documentos.data.length} documento(s)` : "os documentos"}
            </strong>{" "}
            desta obra. Contratos, notas fiscais, locais de ponto e processos
            continuam existindo, mas perdem o vínculo com a obra.
          </p>
          <p className="mt-2">
            Para confirmar, digite o código <span className="font-mono font-semibold">{obra.codigo}</span>:
          </p>
          <form action={excluirObra} className="mt-2 flex flex-wrap items-center gap-2">
            <input type="hidden" name="id" value={obra.id} />
            <input type="hidden" name="codigo" value={obra.codigo} />
            <input
              name="confirmacao"
              required
              autoComplete="off"
              placeholder={obra.codigo}
              className={`${inputCls} font-mono`}
            />
            <button
              type="submit"
              className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700"
            >
              Excluir definitivamente
            </button>
          </form>
        </div>
      </details>
    </div>
  );
}
