/**
 * Tipos e helpers compartilhados pela lista (/obras) e pelo detalhe
 * (/obras/[id]). Nao e rota: so `page.tsx` vira pagina no app router.
 */
import { ApiError, apiFetch } from "@/lib/api";

export type Obra = {
  id: number;
  codigo: string;
  nome: string;
  cliente: string | null;
  uf: string | null;
  cidade: string | null;
  status: string;
  data_inicio: string | null;
  encerramento_previsto: string | null;
  data_encerramento: string | null;
  observacoes: string | null;
};

export const STATUS_OBRA = ["ativa", "suspensa", "encerrada"] as const;

export const STATUS_BADGE: Record<string, string> = {
  ativa: "bg-emerald-100 text-emerald-700",
  encerrada: "bg-slate-200 text-slate-600",
  suspensa: "bg-amber-100 text-amber-700",
};

/** Resultado de leitura que distingue "API falhou" de "lista vazia". */
export type Carga<T> = { ok: true; data: T } | { ok: false; erro: string };

export async function carregar<T>(path: string): Promise<Carga<T>> {
  try {
    return { ok: true, data: await apiFetch<T>(path) };
  } catch (e) {
    return { ok: false, erro: detalheErro(e) };
  }
}

export function detalheErro(e: unknown): string {
  if (!(e instanceof ApiError)) return "API fora do ar ou sem resposta";
  try {
    const detail = (JSON.parse(e.body) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d as { msg?: string }).msg ?? JSON.stringify(d))
        .join("; ");
    }
    return e.body || `HTTP ${e.status}`;
  } catch {
    return e.body || `HTTP ${e.status}`;
  }
}

export function dataBr(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

export function local(o: Pick<Obra, "cidade" | "uf">): string {
  if (o.cidade && o.uf) return `${o.cidade}/${o.uf}`;
  return o.uf || o.cidade || "—";
}

/** Le um campo de texto do form; string vazia vira null (limpa na API). */
export function campo(formData: FormData, nome: string): string | null {
  return String(formData.get(nome) ?? "").trim() || null;
}
