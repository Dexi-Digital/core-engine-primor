import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ApiError, apiFetch } from "@/lib/api";

const nav = [
  { href: "/diagnostico", label: "Diagnóstico" },
  { href: "/obras", label: "Obras" },
  { href: "/rh", label: "RH / DP" },
  { href: "/manutencao", label: "Manutenção" },
  { href: "/financeiro", label: "Financeiro" },
  { href: "/fiscal/documentos", label: "Fiscal (Domínio)" },
  { href: "/licitacoes", label: "Licitações" },
  { href: "/juridico", label: "Jurídico" },
];

type CurrentUser = {
  id: number;
  email: string;
  nome: string;
  role: string;
};

async function loadCurrentUser(): Promise<CurrentUser> {
  try {
    return await apiFetch<CurrentUser>("/api/v1/auth/me");
  } catch (err) {
    // Token expirou / invalidou -- middleware checa presenca, mas a
    // API ainda pode rejeitar (ex.: user removido). Manda pro login
    // pra renovar a sessao.
    if (err instanceof ApiError && err.status === 401) {
      redirect("/logout");
    }
    throw err;
  }
}

export default async function DashboardLayout({
  children,
}: {
  children: ReactNode;
}) {
  const user = await loadCurrentUser();

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white px-4 py-6">
        <Link
          href="/"
          className="block text-sm font-bold uppercase tracking-widest"
        >
          Motor Central
        </Link>
        <nav className="mt-8 flex flex-1 flex-col gap-1 text-sm">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-md px-3 py-2 text-slate-700 hover:bg-slate-100"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="mt-4 border-t border-slate-200 pt-4 text-xs text-slate-500">
          <p className="font-semibold text-slate-700">{user.nome}</p>
          <p className="truncate" title={user.email}>
            {user.email}
          </p>
          <p className="mt-1 inline-block rounded bg-slate-100 px-2 py-0.5 text-[10px] uppercase tracking-wider">
            {user.role}
          </p>
          <a
            href="/logout"
            className="mt-3 block rounded-md border border-slate-300 px-3 py-1.5 text-center text-xs font-medium text-slate-700 hover:bg-slate-100"
          >
            Sair
          </a>
        </div>
      </aside>
      <main className="flex-1 px-8 py-8">{children}</main>
    </div>
  );
}
