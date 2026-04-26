/**
 * GET /logout: limpa cookies e redireciona para /login.
 * Como o middleware ja garante token presente em todas as paginas
 * autenticadas, basta um link <a href="/logout"> no header.
 */
import { NextResponse } from "next/server";

import { AUTH_COOKIE_NAMES } from "@/lib/auth";

export async function GET(req: Request) {
  const res = NextResponse.redirect(new URL("/login", req.url));
  // Em route handlers, manipulamos cookies via response (e nao via
  // `cookies()` do next/headers, que la e read-only fora de Server
  // Actions / Route Handlers POST).
  res.cookies.delete(AUTH_COOKIE_NAMES.access);
  res.cookies.delete(AUTH_COOKIE_NAMES.refresh);
  return res;
}
