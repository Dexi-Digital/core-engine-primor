/**
 * Route handler do PWA mobile -- proxy autenticado para a API.
 *
 * O cliente (form/PWA) NAO fala com FastAPI direto: passa por aqui
 * para que o cookie HttpOnly `mc_access_token` seja lido e injetado
 * como Bearer no header. Beneficios:
 *   - Token nunca exposto ao JS do browser (XSS-safe).
 *   - Service worker continua interceptando GET /m/api/* sem
 *     precisar de logica especial para auth.
 *   - URL relativa (`/m/api/...`) funciona offline-first sem
 *     depender de NEXT_PUBLIC_API_BASE_URL.
 *
 * Usamos `fetch` direto (em vez de `apiFetch` do lib/api.ts) para
 * preservar o status code da API: 201 quando a parte e criada nova,
 * 200 quando ja existia (idempotencia via client_uuid). `apiFetch`
 * descartaria essa distincao.
 */
import { NextResponse } from "next/server";

import { getAccessToken } from "@/lib/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function POST(req: Request) {
  let payload: unknown;
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json(
      { detail: "JSON invalido" },
      { status: 400 },
    );
  }

  const token = await getAccessToken();
  if (!token) {
    return NextResponse.json(
      { detail: "nao autenticado" },
      { status: 401 },
    );
  }

  // Sem try/catch o fetch joga em caso de API down/DNS-fail e o
  // Next.js devolve 500 HTML generico -- form.tsx so cai no fallback
  // de fila offline em 503 (sw) ou em catch do fetch dele. Devolvemos
  // 503 explicitamente aqui para preservar o contrato offline-first
  // quando o backend FastAPI esta fora mas o Next.js esta de pe.
  let upstream: globalThis.Response;
  try {
    upstream = await fetch(
      `${API_BASE}/api/v1/manutencao-frota/partes-diarias/manual`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      },
    );
  } catch {
    return NextResponse.json(
      { detail: "API indisponivel -- tente novamente" },
      { status: 503 },
    );
  }

  // Stream-through: preservamos o status (200=duplicata/idempotente,
  // 201=nova; 422=validacao) para o caller (form/queue) decidir UI.
  // Body pode ser nao-JSON em erros 5xx -- tentamos JSON, senao texto.
  const text = await upstream.text();
  let body: unknown;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  return NextResponse.json(body, { status: upstream.status });
}
