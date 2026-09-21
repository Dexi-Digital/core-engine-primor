/**
 * Funcionário — o cadastro único da admissão.
 *
 * Esta página não existia: a lista de funcionários e a jornada de
 * admissão apontavam para `/rh/funcionarios/{id}` e caíam em 404. É aqui
 * que a promessa "digita uma vez, reflete nos outros sistemas" vira
 * tela: dados, dossiê (CEP, CPF, CNPJ) e o painel de reflexo por
 * sistema — com a pré-visualização do envio à OnSafety sem enviar.
 */
import Link from "next/link";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { SubNav } from "@/components/ui/sub-nav";
import { ApiError, apiFetch } from "@/lib/api";

import { RH_SUBNAV } from "../../subnav";

type EmpregoAnterior = {
  id: number;
  empresa_cnpj: string | null;
  empresa_razao_social: string | null;
  cargo: string | null;
  inicio: string | null;
  fim: string | null;
};

type Employee = {
  id: number;
  cpf: string;
  matricula: string | null;
  nome_completo: string;
  cargo: string;
  obra: string | null;
  setor: string | null;
  status: string;
  data_admissao: string | null;
  data_nascimento: string | null;
  rg: string | null;
  pis_pasep: string | null;
  ctps_numero: string | null;
  ctps_serie: string | null;
  nome_mae: string | null;
  telefone: string | null;
  email: string | null;
  cep: string | null;
  logradouro: string | null;
  numero: string | null;
  complemento: string | null;
  bairro: string | null;
  cidade: string | null;
  uf: string | null;
  aso_validade: string | null;
  empregos_anteriores: EmpregoAnterior[];
};

type Etapa = {
  chave: string;
  titulo: string;
  estado: "ok" | "atencao" | "bloqueado" | "pendente";
  detalhe: string | null;
};

type Preview = {
  ambiente: "mock" | "homologacao" | "producao";
  pode_enviar: boolean;
  payload: Record<string, unknown> | null;
  etapas: Etapa[];
  ultimo_envio: {
    status: string;
    em: string | null;
    external_id: string | null;
    source: string | null;
    erro: string | null;
  } | null;
};

type Sync = {
  id: number;
  status: string;
  external_id: string | null;
  source: string | null;
  error_msg: string | null;
  executed_at: string;
};

type PainelItem = { employee_id: number; etapa: string; pendencias: string[] };

export const dynamic = "force-dynamic";

async function seguro<T>(path: string): Promise<T | null> {
  try {
    return await apiFetch<T>(path);
  } catch {
    return null;
  }
}

function detalheErro(e: unknown): string {
  if (!(e instanceof ApiError)) return "erro inesperado";
  try {
    return (JSON.parse(e.body) as { detail?: string }).detail ?? e.body;
  } catch {
    return e.body || `HTTP ${e.status}`;
  }
}

function volta(id: string, msg?: string, erro?: string): never {
  const q = new URLSearchParams();
  if (msg) q.set("msg", msg);
  if (erro) q.set("erro", erro);
  revalidatePath(`/rh/funcionarios/${id}`);
  redirect(`/rh/funcionarios/${id}${q.size ? `?${q}` : ""}`);
}

const CAMPOS_TEXTO = [
  "nome_completo", "matricula", "cargo", "obra", "setor", "rg", "pis_pasep",
  "ctps_numero", "ctps_serie", "nome_mae", "telefone", "email",
  "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf",
] as const;
const CAMPOS_DATA = ["data_admissao", "data_nascimento"] as const;

async function salvar(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  const payload: Record<string, unknown> = {};
  for (const c of CAMPOS_TEXTO) {
    if (formData.has(c)) payload[c] = String(formData.get(c) ?? "").trim() || null;
  }
  for (const c of CAMPOS_DATA) {
    if (formData.has(c)) payload[c] = String(formData.get(c) ?? "").trim() || null;
  }
  try {
    await apiFetch(`/api/v1/dp-sesmt/employees/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  } catch (e) {
    volta(id, undefined, `Não salvou: ${detalheErro(e)}`);
  }
  volta(id, "Cadastro salvo.");
}

async function buscarCep(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  const cep = String(formData.get("cep") ?? "").replace(/\D/g, "");
  if (!id || cep.length !== 8) volta(id, undefined, "Informe um CEP com 8 dígitos.");
  try {
    const r = await apiFetch<{
      cep: string; logradouro: string | null; bairro: string | null;
      cidade: string | null; uf: string | null;
    }>(`/api/v1/dp-sesmt/dossie/cep/${cep}?employee_id=${id}`);
    // Preenche o endereço a partir do CEP -- é o "desenvolva o que dá":
    // ViaCEP é público e funciona hoje.
    await apiFetch(`/api/v1/dp-sesmt/employees/${id}`, {
      method: "PUT",
      body: JSON.stringify({
        cep: r.cep, logradouro: r.logradouro, bairro: r.bairro,
        cidade: r.cidade, uf: r.uf,
      }),
    });
  } catch (e) {
    volta(id, undefined, `CEP: ${detalheErro(e)}`);
  }
  volta(id, "Endereço preenchido a partir do CEP. Confira e informe o número.");
}

async function consultarCpf(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  const cpf = String(formData.get("cpf") ?? "").replace(/\D/g, "");
  try {
    const r = await apiFetch<{
      nome: string | null; situacao_cpf: string | null;
      data_nascimento: string | null; source: string;
    }>(`/api/v1/dp-sesmt/dossie/cpf/${cpf}?employee_id=${id}`);
    const simulado = r.source.endsWith("_mock");
    volta(
      id,
      `${simulado ? "SIMULAÇÃO (sem token DirectData): " : ""}CPF ${
        r.situacao_cpf ?? "sem situação"
      } · nome na base: ${r.nome ?? "—"} · nascimento: ${r.data_nascimento ?? "—"}`,
    );
  } catch (e) {
    volta(id, undefined, `CPF: ${detalheErro(e)}`);
  }
}

async function buscarCnpj(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  const cnpj = String(formData.get("cnpj") ?? "").replace(/\D/g, "");
  if (cnpj.length !== 14) volta(id, undefined, "Informe um CNPJ com 14 dígitos.");
  try {
    const r = await apiFetch<{
      cnpj: string; razao_social: string | null; situacao_cadastral: string | null;
    }>(`/api/v1/dp-sesmt/dossie/cnpj/${cnpj}?employee_id=${id}`);
    volta(
      id,
      `CNPJ ${r.cnpj}: ${r.razao_social ?? "sem razão social"} (${
        r.situacao_cadastral ?? "situação não informada"
      }). Se for empregador anterior, adicione abaixo.`,
    );
  } catch (e) {
    volta(id, undefined, `CNPJ: ${detalheErro(e)}`);
  }
}

async function adicionarEmprego(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  const cnpj = String(formData.get("empresa_cnpj") ?? "").replace(/\D/g, "") || null;
  const razao = String(formData.get("empresa_razao_social") ?? "").trim() || null;
  if (!id || (!cnpj && !razao)) volta(id, undefined, "Informe CNPJ ou razão social.");
  try {
    const atual = await apiFetch<Employee>(`/api/v1/dp-sesmt/employees/${id}`);
    const lista = atual.empregos_anteriores.map((e) => ({
      empresa_cnpj: e.empresa_cnpj, empresa_razao_social: e.empresa_razao_social,
      cargo: e.cargo, inicio: e.inicio, fim: e.fim,
    }));
    lista.push({
      empresa_cnpj: cnpj, empresa_razao_social: razao,
      cargo: String(formData.get("cargo") ?? "").trim() || null,
      inicio: String(formData.get("inicio") ?? "").trim() || null,
      fim: String(formData.get("fim") ?? "").trim() || null,
    });
    await apiFetch(`/api/v1/dp-sesmt/employees/${id}`, {
      method: "PUT",
      body: JSON.stringify({ empregos_anteriores: lista }),
    });
  } catch (e) {
    volta(id, undefined, `Emprego anterior: ${detalheErro(e)}`);
  }
  volta(id, "Emprego anterior adicionado.");
}

async function enviarOnsafety(formData: FormData): Promise<void> {
  "use server";
  const id = String(formData.get("id") ?? "");
  try {
    await apiFetch(`/api/v1/dp-sesmt/employees/${id}/sync-onsafety`, { method: "POST" });
  } catch (e) {
    volta(id, undefined, `OnSafety: ${detalheErro(e)}`);
  }
  volta(id, "Envio à OnSafety registrado. Veja o resultado no histórico abaixo.");
}

function dataBr(iso: string | null): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

function quando(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "short",
  });
}

const ESTADO: Record<Etapa["estado"], { rotulo: string; classe: string }> = {
  ok: { rotulo: "ok", classe: "bg-emerald-100 text-emerald-800" },
  atencao: { rotulo: "atenção", classe: "bg-amber-100 text-amber-800" },
  bloqueado: { rotulo: "bloqueado", classe: "bg-red-100 text-red-800" },
  pendente: { rotulo: "ainda não", classe: "bg-slate-100 text-slate-600" },
};

function Campo({
  name, label, value, type = "text", placeholder,
}: {
  name: string; label: string; value: string | null; type?: string; placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span className="font-medium uppercase tracking-wide text-slate-500">{label}</span>
      <input
        name={name}
        type={type}
        defaultValue={value ?? ""}
        placeholder={placeholder}
        className="rounded border border-slate-300 px-2 py-1 text-sm"
      />
    </label>
  );
}

export default async function FuncionarioPage(props: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await props.params;
  const sp = await props.searchParams;
  const msg = typeof sp.msg === "string" ? sp.msg : "";
  const erro = typeof sp.erro === "string" ? sp.erro : "";

  const [emp, preview, syncs, painel] = await Promise.all([
    seguro<Employee>(`/api/v1/dp-sesmt/employees/${id}`),
    seguro<Preview>(`/api/v1/dp-sesmt/employees/${id}/sync-onsafety/preview`),
    seguro<Sync[]>(`/api/v1/dp-sesmt/employees/${id}/sync-onsafety`),
    seguro<{ itens: PainelItem[] }>("/api/v1/dp-sesmt/admissao/painel"),
  ]);

  if (!emp) {
    return (
      <div className="flex flex-col gap-6">
        <SubNav items={RH_SUBNAV} />
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900">
          Funcionário não encontrado (ou API fora do ar).{" "}
          <Link href="/rh/funcionarios" className="underline">Voltar à lista</Link>
        </div>
      </div>
    );
  }

  const jornada = painel?.itens.find((i) => i.employee_id === emp.id) ?? null;

  return (
    <div className="flex flex-col gap-6">
      <SubNav items={RH_SUBNAV} />

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/rh/funcionarios" className="text-xs text-slate-500 hover:underline">
            ← Funcionários
          </Link>
          <h1 className="text-2xl font-semibold">{emp.nome_completo}</h1>
          <p className="mt-1 text-sm text-slate-600">
            {emp.cargo} · {emp.obra ?? "sem obra"} · CPF {emp.cpf} · {emp.status}
          </p>
        </div>
        {jornada && (
          <Link
            href="/rh/admissoes"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
          >
            Admissão: {jornada.etapa.replace("_", " ")}
          </Link>
        )}
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

      {jornada && jornada.pendencias.length > 0 && (
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm">
          <h2 className="font-semibold text-amber-900">
            Falta para a admissão avançar
          </h2>
          <ul className="mt-2 list-inside list-disc text-amber-900">
            {jornada.pendencias.map((p) => <li key={p}>{p}</li>)}
          </ul>
        </section>
      )}

      {/* ---------------- Reflexo nos sistemas ---------------- */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Reflexo nos outros sistemas</h2>
        <p className="mt-1 text-xs text-slate-500">
          O cadastro é feito uma vez aqui. Cada sistema abaixo mostra em que pé
          está o espelhamento — e o que impede, quando impede.
        </p>

        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          {/* OnSafety */}
          <div className="rounded-lg border border-slate-200 p-3 lg:col-span-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">OnSafety (SST)</h3>
              {preview && (
                <span className={`rounded px-2 py-0.5 text-xs ${
                  preview.ambiente === "producao"
                    ? "bg-red-100 text-red-800"
                    : preview.ambiente === "mock"
                      ? "bg-slate-100 text-slate-700"
                      : "bg-sky-100 text-sky-800"
                }`}>
                  {preview.ambiente === "producao"
                    ? "base REAL do cliente"
                    : preview.ambiente === "mock"
                      ? "simulação (sem token)"
                      : "homologação"}
                </span>
              )}
            </div>

            {!preview ? (
              <p className="mt-2 text-sm text-slate-500">
                Não consegui montar a pré-visualização (falha de requisição).
              </p>
            ) : (
              <>
                <ol className="mt-3 space-y-2">
                  {preview.etapas.map((e, i) => (
                    <li key={e.chave} className="flex gap-3 text-sm">
                      <span className="w-5 shrink-0 text-right text-slate-400">{i + 1}.</span>
                      <span className="min-w-0 flex-1">
                        <span className="font-medium">{e.titulo}</span>{" "}
                        <span className={`rounded px-1.5 py-0.5 text-[11px] ${ESTADO[e.estado].classe}`}>
                          {ESTADO[e.estado].rotulo}
                        </span>
                        {e.detalhe && (
                          <span className="block text-xs text-slate-500">{e.detalhe}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ol>

                {preview.payload && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs text-slate-600">
                      Ver exatamente o que seria enviado
                    </summary>
                    <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-2 text-[11px]">
                      {JSON.stringify(preview.payload, null, 2)}
                    </pre>
                  </details>
                )}

                <form action={enviarOnsafety} className="mt-3 flex items-center gap-3">
                  <input type="hidden" name="id" value={emp.id} />
                  <button
                    type="submit"
                    disabled={!preview.pode_enviar}
                    className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:bg-slate-300"
                  >
                    {preview.ambiente === "producao"
                      ? "Enviar — cria o trabalhador na base REAL"
                      : preview.ambiente === "mock"
                        ? "Enviar (simulação)"
                        : "Enviar para homologação"}
                  </button>
                  <span className="text-xs text-slate-500">
                    {preview.pode_enviar
                      ? "Idempotente: enviar de novo atualiza, não duplica."
                      : "Bloqueado pelas etapas acima. As etapas ficam visíveis mesmo assim."}
                  </span>
                </form>
              </>
            )}
          </div>

          {/* Os outros dois */}
          <div className="space-y-3">
            <div className="rounded-lg border border-slate-200 p-3">
              <h3 className="text-sm font-semibold">Tangerino / Sólides (ponto)</h3>
              <p className="mt-1 text-xs text-slate-600">
                Só leitura hoje: o Motor Central importa funcionários e batidas,
                mas ainda não sabemos se a API deles aceita criar funcionário.
                Pergunta enviada ao suporte da Sólides.
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <h3 className="text-sm font-semibold">Domínio (folha / contabilidade)</h3>
              <p className="mt-1 text-xs text-slate-600">
                Sem API nem importação. O reflexo é o <strong>kit de admissão</strong>,
                gerado na jornada — em lote por obra. Aguardando resposta se o
                Domínio importa eSocial S-2200 em XML, o que eliminaria a
                digitação.
              </p>
              <Link href="/rh/admissoes" className="mt-1 inline-block text-xs text-blue-700 hover:underline">
                Ir para a jornada →
              </Link>
            </div>
          </div>
        </div>

        {/* histórico */}
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Histórico de envios à OnSafety
          </h3>
          {(syncs ?? []).length === 0 ? (
            <p className="mt-1 text-sm text-slate-400">Nenhum envio ainda.</p>
          ) : (
            <ul className="mt-1 divide-y divide-slate-100 text-sm">
              {(syncs ?? []).slice(0, 8).map((s) => (
                <li key={s.id} className="flex gap-3 py-1.5">
                  <span className="w-32 shrink-0 text-slate-500">{quando(s.executed_at)}</span>
                  <span className={`shrink-0 rounded px-1.5 text-xs ${
                    s.status === "ok" ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"
                  }`}>{s.status}</span>
                  <span className="min-w-0 flex-1 truncate text-slate-600">
                    {s.status === "ok"
                      ? `id ${s.external_id ?? "—"} · ${s.source ?? ""}`
                      : s.error_msg}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* ---------------- Dossiê ---------------- */}
      <section className="grid gap-4 md:grid-cols-3">
        <form action={buscarCep} className="rounded-lg border border-slate-200 bg-white p-4">
          <input type="hidden" name="id" value={emp.id} />
          <h2 className="text-sm font-semibold">Endereço pelo CEP</h2>
          <p className="mt-1 text-xs text-slate-500">
            ViaCEP, público. Preenche logradouro, bairro, cidade e UF.
          </p>
          <div className="mt-2 flex gap-2">
            <input name="cep" defaultValue={emp.cep ?? ""} placeholder="00000-000"
              className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm" />
            <button type="submit" className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">
              Buscar
            </button>
          </div>
        </form>

        <form action={consultarCpf} className="rounded-lg border border-slate-200 bg-white p-4">
          <input type="hidden" name="id" value={emp.id} />
          <input type="hidden" name="cpf" value={emp.cpf} />
          <h2 className="text-sm font-semibold">Situação do CPF</h2>
          <p className="mt-1 text-xs text-slate-500">
            Dígitos verificadores conferidos localmente. A consulta à Receita
            (DirectData) responde em <strong>simulação</strong> até o token chegar
            — o resultado vem marcado como tal.
          </p>
          <button type="submit" className="mt-2 rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">
            Consultar {emp.cpf}
          </button>
        </form>

        <form action={buscarCnpj} className="rounded-lg border border-slate-200 bg-white p-4">
          <input type="hidden" name="id" value={emp.id} />
          <h2 className="text-sm font-semibold">Empregador anterior (CNPJ)</h2>
          <p className="mt-1 text-xs text-slate-500">
            BrasilAPI, público. Confere razão social e situação cadastral.
          </p>
          <div className="mt-2 flex gap-2">
            <input name="cnpj" placeholder="00.000.000/0000-00"
              className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm" />
            <button type="submit" className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">
              Buscar
            </button>
          </div>
        </form>
      </section>

      {/* ---------------- Dados ---------------- */}
      <form action={salvar} className="rounded-lg border border-slate-200 bg-white p-4">
        <input type="hidden" name="id" value={emp.id} />
        <h2 className="text-sm font-semibold">Dados cadastrais</h2>
        <p className="mt-1 text-xs text-slate-500">
          Os campos que a admissão exige estão marcados com *.
        </p>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <Campo name="nome_completo" label="Nome completo *" value={emp.nome_completo} />
          <Campo name="matricula" label="Matrícula" value={emp.matricula} />
          <Campo name="cargo" label="Cargo *" value={emp.cargo} />
          <Campo name="obra" label="Obra" value={emp.obra} />
          <Campo name="setor" label="Setor" value={emp.setor} />
          <Campo name="data_admissao" label="Data de admissão *" value={emp.data_admissao} type="date" />
          <Campo name="data_nascimento" label="Data de nascimento *" value={emp.data_nascimento} type="date" />
          <Campo name="rg" label="RG *" value={emp.rg} />
          <Campo name="pis_pasep" label="PIS/PASEP *" value={emp.pis_pasep} />
          <Campo name="ctps_numero" label="CTPS — número *" value={emp.ctps_numero} />
          <Campo name="ctps_serie" label="CTPS — série *" value={emp.ctps_serie} />
          <Campo name="nome_mae" label="Nome da mãe *" value={emp.nome_mae} />
          <Campo name="telefone" label="Telefone" value={emp.telefone} />
          <Campo name="email" label="E-mail" value={emp.email} type="email" />
        </div>
        <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Endereço</h3>
        <div className="mt-2 grid gap-3 md:grid-cols-4">
          <Campo name="cep" label="CEP" value={emp.cep} />
          <Campo name="logradouro" label="Logradouro" value={emp.logradouro} />
          <Campo name="numero" label="Número" value={emp.numero} />
          <Campo name="complemento" label="Complemento" value={emp.complemento} />
          <Campo name="bairro" label="Bairro" value={emp.bairro} />
          <Campo name="cidade" label="Cidade" value={emp.cidade} />
          <Campo name="uf" label="UF" value={emp.uf} />
        </div>
        <button type="submit" className="mt-4 rounded bg-slate-900 px-4 py-1.5 text-sm text-white">
          Salvar
        </button>
      </form>

      {/* ---------------- Empregos anteriores ---------------- */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Empregos anteriores</h2>
        {emp.empregos_anteriores.length === 0 ? (
          <p className="mt-1 text-sm text-slate-400">Nenhum registrado.</p>
        ) : (
          <ul className="mt-2 divide-y divide-slate-100 text-sm">
            {emp.empregos_anteriores.map((e) => (
              <li key={e.id} className="py-1.5">
                <span className="font-medium">{e.empresa_razao_social ?? "—"}</span>
                <span className="text-slate-500"> · {e.empresa_cnpj ?? "sem CNPJ"} · {e.cargo ?? "—"} · {dataBr(e.inicio)} a {dataBr(e.fim)}</span>
              </li>
            ))}
          </ul>
        )}
        <form action={adicionarEmprego} className="mt-3 grid gap-2 md:grid-cols-6">
          <input type="hidden" name="id" value={emp.id} />
          <input name="empresa_cnpj" placeholder="CNPJ" className="rounded border border-slate-300 px-2 py-1 text-sm" />
          <input name="empresa_razao_social" placeholder="Razão social" className="rounded border border-slate-300 px-2 py-1 text-sm md:col-span-2" />
          <input name="cargo" placeholder="Cargo" className="rounded border border-slate-300 px-2 py-1 text-sm" />
          <input name="inicio" type="date" className="rounded border border-slate-300 px-2 py-1 text-sm" />
          <div className="flex gap-2">
            <input name="fim" type="date" className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm" />
            <button type="submit" className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">+</button>
          </div>
        </form>
      </section>
    </div>
  );
}
