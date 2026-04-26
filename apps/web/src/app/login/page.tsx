import { redirect } from "next/navigation";

import { ApiError, apiFetchAnonymous } from "@/lib/api";
import { setAuthCookies, type TokenPair, getAccessToken } from "@/lib/auth";

type LoginPageProps = {
  searchParams: Promise<{ error?: string; next?: string }>;
};

async function loginAction(formData: FormData) {
  "use server";
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = String(formData.get("next") ?? "/") || "/";

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
  const next = params.next ?? "/";

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <form
        action={loginAction}
        className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm"
      >
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
          ZAG / PRIMOR
        </p>
        <h1 className="mt-1 text-2xl font-bold">Motor Central</h1>
        <p className="mt-1 text-sm text-slate-500">Entre com sua conta corporativa.</p>

        <input type="hidden" name="next" value={next} />

        <label className="mt-6 block text-sm font-medium text-slate-700">
          Email
          <input
            name="email"
            type="email"
            autoComplete="username"
            required
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
          />
        </label>

        <label className="mt-4 block text-sm font-medium text-slate-700">
          Senha
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
          />
        </label>

        {errorMessage ? (
          <p
            className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700"
            role="alert"
          >
            {errorMessage}
          </p>
        ) : null}

        <button
          type="submit"
          className="mt-6 w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
        >
          Entrar
        </button>
      </form>
    </main>
  );
}
