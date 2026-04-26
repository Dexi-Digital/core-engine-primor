/**
 * Auth helpers (server-side).
 *
 * Token storage: cookie HTTP-only `mc_access_token` (e refresh em
 * `mc_refresh_token`). Mais seguro que localStorage -- nao acessivel
 * por JS, evita XSS-vazamento.
 *
 * As helpers aqui rodam SOMENTE em Server Components / Server Actions /
 * Route Handlers / Middleware. Para fetch a partir do client, exponha
 * uma Server Action wrapper.
 */
import { cookies } from "next/headers";

const ACCESS_COOKIE = "mc_access_token";
const REFRESH_COOKIE = "mc_refresh_token";

// Defaults conservadores: HttpOnly + SameSite=lax. Em prod (HTTPS) o
// cookie tambem fica `Secure`. Em dev (HTTP localhost) Secure quebra,
// entao detectamos pelo NODE_ENV.
const COOKIE_BASE = {
  httpOnly: true,
  sameSite: "lax" as const,
  path: "/",
  secure: process.env.NODE_ENV === "production",
};

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

export async function setAuthCookies(tokens: TokenPair): Promise<void> {
  const store = await cookies();
  store.set(ACCESS_COOKIE, tokens.access_token, {
    ...COOKIE_BASE,
    maxAge: tokens.expires_in,
  });
  store.set(REFRESH_COOKIE, tokens.refresh_token, {
    ...COOKIE_BASE,
    // Refresh tem TTL maior (default 7d). Nao mandamos expires_in
    // do refresh no payload (TokenPair so reporta o do access),
    // entao deixamos "session-ish" -- o backend invalida quando o
    // jwt-refresh expira.
    maxAge: 60 * 60 * 24 * 7,
  });
}

export async function clearAuthCookies(): Promise<void> {
  const store = await cookies();
  store.delete(ACCESS_COOKIE);
  store.delete(REFRESH_COOKIE);
}

export async function getAccessToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(ACCESS_COOKIE)?.value ?? null;
}

export async function getRefreshToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(REFRESH_COOKIE)?.value ?? null;
}

export const AUTH_COOKIE_NAMES = {
  access: ACCESS_COOKIE,
  refresh: REFRESH_COOKIE,
} as const;
