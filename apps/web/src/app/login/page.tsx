import { redirect } from "next/navigation";

import { ApiError, apiFetchAnonymous } from "@/lib/api";
import { setAuthCookies, type TokenPair, getAccessToken } from "@/lib/auth";

type LoginPageProps = {
  searchParams: Promise<{ error?: string; next?: string }>;
};

/**
 * Sanitiza o `next` para prevenir open-redirect: so aceita paths
 * relativos (sem protocol/host). `//evil.com` em browsers vira
 * `https://evil.com`, entao tambem rejeitamos.
 *
 * Tambem rejeitamos /login e /logout como destinos -- caso contrario
 * o usuario apos logar voltaria pra tela de login (loop) ou seria
 * deslogado de imediato.
 */
function sanitizeNext(raw: string): string {
  if (!raw.startsWith("/") || raw.startsWith("//")) {
    return "/";
  }
  const bare = raw.split("?")[0];
  if (bare === "/login" || bare === "/logout") {
    return "/";
  }
  return raw;
}

async function loginAction(formData: FormData) {
  "use server";
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = sanitizeNext(String(formData.get("next") ?? "/"));

  if (!email || !password) {
    redirect(`/login?error=missing&next=${encodeURIComponent(next)}`);
  }

  try {
    const tokens = await apiFetchAnonymous<TokenPair>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    await setAuthCookies(tokens);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      redirect(`/login?error=invalid&next=${encodeURIComponent(next)}`);
    }
    redirect(`/login?error=server&next=${encodeURIComponent(next)}`);
  }
  redirect(next);
}

const ERROR_MESSAGES: Record<string, string> = {
  missing: "Preencha email e senha.",
  invalid: "Email ou senha incorretos.",
  server: "Erro no servidor. Tente novamente.",
};

export default async function LoginPage({ searchParams }: LoginPageProps) {
  // Se ja esta logado, manda pra home.
  if (await getAccessToken()) {
    redirect("/");
  }
  const params = await searchParams;
  const errorMessage = params.error ? ERROR_MESSAGES[params.error] : null;
  // Tambem sanitizamos no render para nao ecoar `https://evil.com` em
  // qualquer parte do HTML (defense in depth).
  const next = sanitizeNext(params.next ?? "/");

  return (
    <main
      className="flex min-h-screen items-center justify-center px-4"
      style={{
        background:
          "radial-gradient(1200px 600px at 80% -10%, rgba(16,185,129,0.18), transparent 60%), radial-gradient(900px 500px at 10% 110%, rgba(15,118,110,0.15), transparent 60%), linear-gradient(180deg, #0b1220 0%, #0f172a 100%)",
      }}
    >
      <div className="flex w-full max-w-[980px] items-stretch overflow-hidden rounded-2xl shadow-2xl">
        {/* Painel esquerdo — branding */}
        <aside
          className="relative hidden w-[46%] flex-col justify-between p-10 md:flex"
          style={{
            background:
              "linear-gradient(160deg, #0b1220 0%, #111a2e 55%, #0f766e 140%)",
            color: "#e2e8f0",
          }}
        >
          <div className="flex items-center gap-2">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-xl"
              style={{
                background: "linear-gradient(135deg, #10b981, #0f766e)",
                color: "#0b1220",
                fontWeight: 800,
                fontSize: 13,
              }}
            >
              MC
            </span>
            <div>
              <div className="display text-sm font-bold text-white">
                Motor Central
              </div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-slate-400">
                ZAG · PRIMOR
              </div>
            </div>
          </div>

          <div>
            <div
              className="text-[11px] font-semibold uppercase tracking-[0.18em]"
              style={{ color: "#10b981" }}
            >
              Governância documental
            </div>
            <h2
              className="display mt-2 text-3xl font-bold leading-tight text-white"
              style={{ letterSpacing: "-0.02em" }}
            >
              Cada documento,
              <br />
              cada integração,
              <br />
              cada prazo — monitorados.
            </h2>
            <p className="mt-3 text-sm text-slate-300">
              Plataforma única que conecta Domínio, TOTVS, PNCP, OneDrive e
              Infosimples ao ciclo operacional da construtora.
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            {["PNCP", "Detran", "CREA", "OneDrive", "Document AI", "Resend"].map(
              (t) => (
                <span
                  key={t}
                  className="rounded-full px-3 py-1 text-[11px] font-semibold"
                  style={{
                    background: "rgba(16,185,129,0.12)",
                    color: "#6ee7b7",
                    border: "1px solid rgba(16,185,129,0.28)",
                  }}
                >
                  {t}
                </span>
              ),
            )}
          </div>
        </aside>

        {/* Painel direito — form */}
        <form
          action={loginAction}
          className="flex w-full flex-col p-8 md:w-[54%] md:p-10"
          style={{ background: "var(--panel)" }}
        >
          <div
            className="text-[11px] font-semibold uppercase tracking-[0.14em]"
            style={{ color: "var(--fg-subtle)" }}
          >
            Acesso corporativo
          </div>
          <h1
            className="display mt-1 text-2xl font-bold"
            style={{ color: "var(--fg)" }}
          >
            Entrar no Motor Central
          </h1>
          <p className="mt-1 text-sm" style={{ color: "var(--fg-muted)" }}>
            Use o email corporativo Primor.
          </p>

          <input type="hidden" name="next" value={next} />

          <label
            className="mt-6 block text-xs font-semibold uppercase tracking-widest"
            style={{ color: "var(--fg-muted)" }}
          >
            Email
            <input
              name="email"
              type="email"
              autoComplete="username"
              required
              placeholder="nome@primor.com.br"
              className="input mt-1.5"
              style={{ textTransform: "none" }}
            />
          </label>

          <label
            className="mt-4 block text-xs font-semibold uppercase tracking-widest"
            style={{ color: "var(--fg-muted)" }}
          >
            Senha
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              placeholder="••••••••"
              className="input mt-1.5"
            />
          </label>

          {errorMessage ? (
            <p
              className="mt-4 rounded-md px-3 py-2 text-sm"
              style={{
                background: "var(--danger-bg)",
                color: "var(--danger-fg)",
              }}
              role="alert"
            >
              {errorMessage}
            </p>
          ) : null}

          <button type="submit" className="btn btn-primary mt-6 justify-center">
            Entrar
          </button>

          <div
            className="mt-6 text-xs"
            style={{ color: "var(--fg-subtle)" }}
          >
            Governança documental · integrações · auditoria — v0.3.0
          </div>
        </form>
      </div>
    </main>
  );
}
