"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { autorizarDownload, carregarLotes, carregarPrevia, carregarTotvs, confirmarLote, prepararArquivo, reprocessarLote } from "./actions";
import type { Lote, Previa, Totvs } from "./types";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const ROOT = `${API}/api/v1/financeiro/importacoes`;
const brl = (n: string | number) => Number(n).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const STATUS: Record<string, string> = {
  aguardando_arquivo: "Aguardando arquivo", na_fila: "Aguardando validação",
  validado: "Pronto para conferência", invalido: "Corrigir planilha", ativo: "Base ativa",
  historico: "Histórico", erro_fila: "Falha ao enfileirar", erro_processamento: "Falha ao processar",
};
const COLUNAS = [
  ["linha", "Linha"], ["empresa", "Empresa"], ["contraparte", "Cliente / Fornecedor"],
  ["titulo", "Título"], ["data_pagamento", "Pagamento"], ["periodo", "Período"],
  ["data_inclusao_nf", "Inclusão NF"], ["data_emissao", "Emissão"],
  ["valor_original", "Valor original"], ["valor_incremento", "Incremento"],
  ["valor_abatimento", "Abatimento"], ["valor_amortizado", "Amortizado"],
  ["valor_acrescimo", "Acréscimo"], ["valor_deducao", "Dedução"],
  ["valor_pago", "Pago"], ["valor_atual", "Atual"], ["local", "Local / Obra"],
  ["centro_custo", "Centro de custo"], ["id_natureza", "ID Natureza"],
  ["natureza_i", "I"], ["natureza_ii", "II"], ["natureza_iii", "III"],
  ["natureza", "Natureza"], ["valor_apropriado", "Apropriado"],
  ["competencia", "Competência"], ["classificacao", "Classificação"],
];
const button = "rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40";
const input = "rounded-md border border-slate-300 bg-white px-3 py-2 text-sm";

async function fileResponse(response: Response): Promise<void> {
  if (response.ok) return;
  let message = `Falha na transferência (${response.status}).`;
  try { const body = await response.json(); if (typeof body.detail === "string") message = body.detail; } catch { /* proxy */ }
  throw new Error(message);
}

export function Importacoes() {
  const [lotes, setLotes] = useState<Lote[]>([]);
  const [ativo, setAtivo] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [previa, setPrevia] = useState<Previa | null>(null);
  const [filters, setFilters] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [confirmacao, setConfirmacao] = useState(false);
  const [totvs, setTotvs] = useState<Totvs | null>(null);
  const [paginaTotvs, setPaginaTotvs] = useState(1);
  const fileRef = useRef<HTMLInputElement>(null);
  const lote = lotes.find((item) => item.id === selected);
  const ativoLote = lotes.find((item) => item.id === ativo);

  const refresh = useCallback(async () => {
    const result = await carregarLotes();
    if (result.error) { setError(result.error); return; }
    if (!result.data) return;
    setLotes(result.data.data);
    setAtivo(result.data.ativo_id);
    setSelected((current) => current ?? result.data!.ativo_id ?? result.data!.data[0]?.id ?? null);
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    let cancelled = false;
    void carregarTotvs(paginaTotvs).then((result) => {
      if (cancelled) return;
      if (result.data) setTotvs(result.data);
      else setError(result.error);
    });
    return () => { cancelled = true; };
  }, [paginaTotvs]);
  const pendentes = lotes.some((item) => item.status === "na_fila");
  useEffect(() => {
    if (!pendentes) return;
    const timer = setInterval(() => { void refresh(); }, 5000);
    return () => clearInterval(timer);
  }, [pendentes, refresh]);
  useEffect(() => {
    setConfirmacao(false);
  }, [selected, ativo]);
  useEffect(() => {
    let cancelled = false;
    setPrevia(null);
    if (!selected) return;
    void carregarPrevia(selected, `${filters}&page=${page}`).then((result) => {
      if (cancelled) return;
      if (result.error) setError(result.error);
      else if (result.data) setPrevia(result.data);
    });
    return () => { cancelled = true; };
  }, [selected, filters, page, lote?.status]);

  async function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setError(""); setNotice(""); setBusy(true);
    try {
      if (!file.name.toLowerCase().endsWith(".xlsx") || file.size > 30 * 1024 * 1024) {
        throw new Error("Selecione uma planilha XLSX de até 30 MB.");
      }
      setNotice("Conferindo o arquivo…");
      const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const hash = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
      const result = await prepararArquivo(file.name, hash);
      if (!result.data) throw new Error(result.error);
      const { lote: prepared, upload_token } = result.data;
      setSelected(prepared.id); setPage(1);
      if (upload_token) {
        setNotice("Enviando planilha…");
        const response = await fetch(`${ROOT}/${prepared.id}/arquivo`, {
          method: "PUT", headers: { Authorization: `Bearer ${upload_token}`, "Content-Type": "application/octet-stream" }, body: file,
        });
        await fileResponse(response);
        setNotice("Arquivo recebido. A validação pode levar alguns minutos; esta tela atualiza automaticamente.");
      } else {
        setNotice("Este arquivo já possui um lote. Consulte a conferência ou reprocesse se houve falha.");
      }
    } catch (err) { setNotice(""); setError(err instanceof Error ? err.message : "Falha ao enviar arquivo."); }
    finally { setBusy(false); await refresh(); }
  }

  async function confirmar() {
    if (!lote || !confirmacao) return;
    setBusy(true); setError("");
    const result = await confirmarLote(lote.id, ativo);
    if (result.error) setError(result.error);
    else { setNotice("Base do legado atualizada. A versão anterior permanece no histórico."); setConfirmacao(false); }
    setBusy(false); await refresh();
  }
  async function reprocessar() {
    if (!lote) return;
    setBusy(true); setError("");
    const result = await reprocessarLote(lote.id);
    if (result.error) setError(result.error);
    else setNotice("Validação reenfileirada.");
    setBusy(false); await refresh();
  }
  async function download() {
    if (!lote) return;
    setBusy(true); setError("");
    try {
      const auth = await autorizarDownload(lote.id);
      if (!auth.data) throw new Error(auth.error);
      const response = await fetch(`${ROOT}/${lote.id}/arquivo`, { headers: { Authorization: `Bearer ${auth.data.token}` } });
      await fileResponse(response);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `financeiro-${lote.id}.xlsx`;
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (err) { setError(err instanceof Error ? err.message : "Falha ao exportar."); }
    finally { setBusy(false); }
  }

  return <div className="space-y-6">
    {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">{error}</p>}
    {notice && <p role="status" className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">{notice}</p>}
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="font-semibold">Origem TOTVS RM · contas financeiras</h2>
          <p className="text-xs text-slate-500">Leitura do modelo RM existente, separada do legado 90. Os campos disponíveis ainda não compõem o relatório FIN completo por apropriação.</p></div>
        {totvs && <span className="text-sm">{totvs.total.toLocaleString("pt-BR")} lançamentos · valor original {brl(totvs.valor_original)}</span>}
      </div>
      {totvs?.sincronizacoes.length ? <details><summary className="cursor-pointer text-sm underline">Últimas sincronizações do RM</summary>
        <ul className="mt-2 space-y-1 text-xs text-slate-600">{totvs.sincronizacoes.map((run, idx) => <li key={`${run.source}-${run.janela}-${idx}`}>{run.status} · {run.source} · {run.janela} · {run.lidos} lidos · {run.extractor}{run.error_message ? ` · ${run.error_message}` : ""}</li>)}</ul>
      </details> : <p className="text-sm text-slate-500">Ainda não há lançamentos ou sincronizações do TOTVS nesta base.</p>}
      {totvs?.data.length ? <div className="max-h-[24rem] overflow-auto"><table className="w-full whitespace-nowrap text-left text-xs">
        <thead className="sticky top-0 bg-slate-100"><tr>{["ID RM", "Coligada", "Cliente / Fornecedor", "Documento", "Emissão", "Vencimento", "Original", "Pago", "Saldo", "Status", "Extrator"].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead>
        <tbody>{totvs.data.map((row) => <tr key={row.external_id} className="border-b border-slate-100">
          <td className="p-2">{row.external_id}</td><td className="p-2">{row.codcoligada ?? "—"}</td><td className="p-2">{row.contraparte_nome ?? "—"}</td><td className="p-2">{row.contraparte_documento ?? "—"}</td>
          <td className="p-2">{row.data_emissao ?? "—"}</td><td className="p-2">{row.data_vencimento ?? "—"}</td><td className="p-2">{row.valor == null ? "—" : brl(row.valor)}</td>
          <td className="p-2">{row.valor_baixado == null ? "—" : brl(row.valor_baixado)}</td><td className="p-2">{row.saldo == null ? "—" : brl(row.saldo)}</td><td className="p-2">{row.situacao ?? row.status_rm ?? "—"}</td><td className="p-2">{row.extractor}</td>
        </tr>)}</tbody>
      </table></div> : totvs?.total === 0 ? null : <p className="text-sm text-slate-500">Carregando lançamentos TOTVS…</p>}
      {!!totvs?.total && <div className="flex items-center gap-4 text-sm"><button disabled={paginaTotvs === 1} className="disabled:opacity-40" onClick={() => setPaginaTotvs(paginaTotvs - 1)}>← Anterior</button><span>Página {paginaTotvs}</span><button disabled={paginaTotvs * 50 >= totvs.total} className="disabled:opacity-40" onClick={() => setPaginaTotvs(paginaTotvs + 1)}>Próxima →</button></div>}
    </section>
    <form onSubmit={upload} className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
      <label className="block text-sm font-semibold" htmlFor="planilha">Nova versão da base completa · XLSX até 30 MB</label>
      <input ref={fileRef} id="planilha" type="file" accept=".xlsx" required disabled={busy} className="block w-full text-sm" />
      <p className="text-xs text-slate-500">Use a aba Relatório Completo com as 23 colunas originais. As abas de impressão e o resumo não são importados.</p>
      <button className={button} disabled={busy}>Enviar para conferência</button>
    </form>

    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">Histórico de importações</h2>
        <button type="button" onClick={() => void refresh()} className="text-sm underline">Atualizar</button>
      </div>
      {lotes.length === 0 ? <p className="mt-4 text-sm text-slate-500">Nenhuma importação carregada.</p> :
        <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm">
          <thead><tr><th className="p-2">Lote / arquivo</th><th className="p-2">Estado</th><th className="p-2">Linhas</th><th className="p-2">Responsável</th></tr></thead>
          <tbody>{lotes.map((item) => <tr key={item.id} className={selected === item.id ? "bg-blue-50" : "border-t border-slate-100"}>
            <td className="p-2"><button className="text-left underline" onClick={() => { setSelected(item.id); setPage(1); }}>{item.id} · {item.nome_arquivo}</button></td>
            <td className="p-2">{STATUS[item.status] ?? item.status}</td><td className="p-2">{item.linhas.toLocaleString("pt-BR")}</td><td className="p-2">{item.actor}</td>
          </tr>)}</tbody>
        </table></div>}
      <p className="mt-3 text-xs text-slate-500">Últimos 50 lotes. Base ativa: {ativo ? `lote ${ativo}` : "nenhuma"}.</p>
    </section>

    {lote && <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
      <h2 className="font-semibold">Conferência do lote {lote.id} · {STATUS[lote.status]}</h2>
      <p className="text-sm">{lote.linhas.toLocaleString("pt-BR")} linhas · {lote.erros} com erro · {lote.avisos} com aviso</p>
      {lote.mensagem && <p role="alert" className="text-sm text-red-700">{lote.mensagem}</p>}
      {lote.status === "na_fila" && <p className="text-sm text-slate-600">Aguardando conclusão da validação. Se não avançar após 30 minutos, use Reprocessar.</p>}
      {["erro_fila", "erro_processamento", "na_fila"].includes(lote.status) && <button disabled={busy} className={button} onClick={reprocessar}>Reprocessar</button>}
      <div className="grid gap-3 sm:grid-cols-3">{lote.resumo.map((empresa) => <div key={empresa.empresa} className="rounded-lg bg-slate-50 p-3">
        <p className="font-semibold">{empresa.empresa}</p><p className="text-sm">{empresa.linhas.toLocaleString("pt-BR")} apropriações</p>
        <p className="tabular-nums">{brl(empresa.valor_apropriado)}</p>
      </div>)}</div>
      {lote.ocorrencias.length > 0 && <details open={lote.erros > 0} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
        <summary className="cursor-pointer font-medium">Erros e avisos (primeiras 100 ocorrências)</summary>
        <ul className="mt-2 max-h-64 space-y-1 overflow-y-auto">{lote.ocorrencias.map((item, idx) => <li key={idx}>Linha {item.linha} · {item.tipo}: {item.mensagem}</li>)}</ul>
        {lote.erros > 0 && <p className="mt-2">Corrija a planilha e envie o arquivo corrigido. Nenhuma linha deste lote será ativada enquanto houver erros.</p>}
      </details>}
      {["validado", "historico"].includes(lote.status) && <div className="space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-4">
        <p className="text-sm">Ao ativar, este lote passará a representar toda a base do legado 90{ativo ? `, substituindo o lote ${ativo}` : ""}.</p>
        {ativoLote && <p className="text-sm">Empresas da base atual: {ativoLote.resumo.map((r) => r.empresa).join(", ")}. Confira se o novo arquivo cobre todo o histórico necessário.</p>}
        <label className="flex gap-2 text-sm"><input type="checkbox" checked={confirmacao} onChange={(e) => setConfirmacao(e.target.checked)} />Conferi os valores, empresas e avisos e quero ativar esta base completa.</label>
        <button className={button} disabled={busy || !confirmacao} onClick={confirmar}>Ativar base do legado</button>
      </div>}
      {["validado", "ativo", "historico"].includes(lote.status) && <button className={button} disabled={busy} onClick={download}>Baixar Excel completo deste lote</button>}
      <p className="text-xs text-slate-500">Exportação com todos os registros do lote, 23 campos originais e 2 calculados. Os filtros abaixo afetam apenas a consulta.</p>

      <form className="grid gap-2 sm:grid-cols-3" onSubmit={(event) => {
        event.preventDefault(); const data = new FormData(event.currentTarget); const qs = new URLSearchParams();
        for (const [key, value] of data) if (String(value).trim()) qs.set(key, String(value).trim());
        setFilters(qs.toString()); setPage(1);
      }}>
        {[["empresa", "Empresa"], ["local", "Local / Obra"], ["contraparte", "Cliente / Fornecedor"], ["centro_custo", "Centro de custo"], ["natureza", "Descrição da natureza"], ["classificacao", "Classificação"], ["natureza_i", "Natureza I"], ["natureza_ii", "Natureza II"], ["natureza_iii", "Natureza III"]].map(([name, label]) =>
          <input key={name} name={name} placeholder={label} aria-label={label} className={input} />)}
        <select name="campo_data" aria-label="Filtrar por data" className={input} defaultValue="competencia"><option value="competencia">Competência</option><option value="data_emissao">Emissão</option><option value="data_pagamento">Pagamento</option><option value="periodo">Período</option></select>
        <input type="date" name="inicio" aria-label="Data inicial" className={input} /><input type="date" name="fim" aria-label="Data final" className={input} />
        <button className={button}>Aplicar filtros</button>
      </form>
      {previa && <>
        <p className="text-sm">{previa.total.toLocaleString("pt-BR")} apropriações no filtro · Total apropriado: <strong>{brl(previa.valor_apropriado)}</strong></p>
        <div className="max-h-[32rem] overflow-auto"><table className="w-full whitespace-nowrap text-left text-xs">
          <thead className="sticky top-0 bg-slate-100"><tr>{COLUNAS.map(([field, label]) => <th key={field} className="p-2">{label}</th>)}</tr></thead>
          <tbody>{previa.data.map((row) => <tr key={row.linha} className="border-b border-slate-100">{COLUNAS.map(([field]) => <td key={field} className="max-w-72 truncate p-2" title={String(row[field] ?? "")}>{row[field] == null ? "—" : field.startsWith("valor_") ? brl(row[field]) : String(row[field])}</td>)}</tr>)}</tbody>
        </table></div>
        <div className="flex items-center gap-4 text-sm"><button disabled={page === 1} className="disabled:opacity-40" onClick={() => setPage(page - 1)}>← Anterior</button><span>Página {page}</span><button disabled={page * 50 >= previa.total} className="disabled:opacity-40" onClick={() => setPage(page + 1)}>Próxima →</button></div>
      </>}
    </section>}
  </div>;
}
