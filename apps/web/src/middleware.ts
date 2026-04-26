import { NextResponse, type NextRequest } from "next/server";

/**
 * Auth middleware: redireciona requests sem cookie de access token
 * para /login (preservando `?next=` para retornar depois).
 *
 * Whitelist:
 * - /login (pagina de login, obviamente)
 * - /_next/* (assets estaticos do Next)
 * - favicon, /api/health, /api do proprio Next (Server Actions etc.)
 *
 * IMPORTANTE: este middleware so vale para o WEB (Next). A API
 * FastAPI tem seu proprio Depends(get_current_user) por endpoint;
 * o cookie aqui so existe pro browser.
 */

const PUBLIC_PATHS = [
  "/login",
  // /logout precisa estar na whitelist: caso contrario, em fluxo
  // multi-tab o middleware redirecionava p/ /login?next=/logout e
  // logo apos o login o usuario era deslogado de novo.
  "/logout",
  "/favicon.ico",
  // PWA mobile (D5 fase 2): manifest + service worker + icones do
  // app instalavel precisam ser servidos sem cookie. A pagina do
  // form (/m/parte-diaria) continua auth-gated -- so os arquivos
  // do shell PWA sao publicos.
  "/manifest.webmanifest",
  "/sw.js",
  "/m/icons",
];

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  const token = req.cookies.get("mc_access_token")?.value;
  if (!token) {
    const loginUrl = new URL("/login", req.url);
    if (pathname !== "/") {
      loginUrl.searchParams.set("next", pathname + req.nextUrl.search);
    }
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
}

export const config = {
  // Roda em todas as paginas exceto assets do Next (handled internally).
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
