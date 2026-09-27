"use server";

import { ApiError, apiFetch } from "@/lib/api";
import type { Lote, Previa, Resultado, Totvs } from "./types";

const ROOT = "/api/v1/financeiro/importacoes";
async function call<T>(path: string, init?: RequestInit): Promise<Resultado<T>> {
  try {
    return { data: await apiFetch<T>(`${ROOT}${path}`, { ...init, cache: "no-store" }) };
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 401) return { error: "Sessão expirada. Entre novamente." };
      try {
        const body = JSON.parse(error.body);
        if (typeof body.detail === "string") return { error: body.detail };
      } catch { /* Respostas de proxy podem não ser JSON. */ }
    }
    return { error: "Não foi possível completar a operação. Tente novamente." };
  }
}

export async function carregarLotes() {
  return call<{ ativo_id: number | null; data: Lote[] }>("");
}
export async function carregarTotvs(page: number) {
  return call<Totvs>(`/totvs?page=${page}&page_size=50`);
}
export async function prepararArquivo(nome_arquivo: string, sha256: string) {
  return call<{ lote: Lote; upload_token: string | null }>("", {
    method: "POST", body: JSON.stringify({ nome_arquivo, sha256 }),
  });
}
export async function carregarPrevia(id: number, filters: string) {
  if (!Number.isSafeInteger(id) || id <= 0) return { error: "Lote inválido" } as Resultado<Previa>;
  return call<Previa>(`/${id}/previa?${filters}`);
}
export async function confirmarLote(id: number, substituir_lote_id: number | null) {
  return call(`/${id}/confirmar`, { method: "POST", body: JSON.stringify({ substituir_lote_id }) });
}
export async function reprocessarLote(id: number) {
  return call(`/${id}/reprocessar`, { method: "POST" });
}
export async function autorizarDownload(id: number) {
  return call<{ token: string }>(`/${id}/download-token`, { method: "POST" });
}
