/**
 * API client. Em Server Components / Server Actions / Route Handlers,
 * o token de auth e injetado via cookie HTTP-only `mc_access_token`.
 *
 * Em qualquer 401 limpamos o cookie no caller e redirecionamos para
 * /login. Aqui jogamos o erro para o caller decidir (algumas chamadas
 * de Server Action precisam tratar UI antes do redirect).
 */
import { getAccessToken } from "@/lib/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, public body: string) {
    super(`API ${status}: ${body}`);
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getAccessToken();
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (token) {
    headers["authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  // 204 No Content (ex: DELETE endpoints) -> no JSON body to parse.
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

/**
 * Bypass auth -- usado pela rota de login (POST /auth/login),
 * que nao tem token ainda. Identico ao apiFetch sem ler cookie.
 */
export async function apiFetchAnonymous<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...((init?.headers as Record<string, string>) ?? {}),
    },
  });
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}
