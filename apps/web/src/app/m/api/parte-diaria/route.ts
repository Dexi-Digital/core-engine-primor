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
 */
import { NextResponse } from "next/server";

import { apiFetch, ApiError } from "@/lib/api";

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
  try {
    // apiFetch ja injeta auth via cookie + content-type. O backend
    // devolve 200 (idempotente, ja existia) ou 201 (criada agora);
    // apiFetch nao distingue, entao usamos fetch direto pra
    // preservar o status code para o caller decidir UI.
    const data = await apiFetch<unknown>(
      "/api/v1/manutencao-frota/partes-diarias/manual",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
    return NextResponse.json(data, { status: 201 });
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json(
        { detail: err.body },
        { status: err.status },
      );
    }
    return NextResponse.json({ detail: String(err) }, { status: 500 });
  }
}
