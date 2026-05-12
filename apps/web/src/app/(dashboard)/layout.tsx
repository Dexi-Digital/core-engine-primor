import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { SidebarNav } from "@/components/ui/sidebar-nav";
import {
  IconDashboard,
  IconStethoscope,
  IconBuilding,
  IconUsers,
  IconTruck,
  IconDollar,
  IconDoc,
  IconGavel,
  IconShield,
  IconCloud,
  IconLogout,
} from "@/components/ui/icons";

const navGroups = [
  {
    label: "Visão geral",
    items: [
      { href: "/", label: "Comando Central", icon: <IconDashboard /> },
      { href: "/diagnostico", label: "Diagnóstico", icon: <IconStethoscope /> },
      {
        href: "/diagnostico/onedrive",
        label: "OneDrive (Estrutura)",
        icon: <IconCloud />,
      },
      { href: "/obras", label: "Obras", icon: <IconBuilding /> },
    ],
  },
  {
    label: "Operação",
    items: [
      { href: "/rh", label: "RH / DP", icon: <IconUsers /> },
      { href: "/manutencao", label: "Manutenção & Frota", icon: <IconTruck /> },
    ],
  },
  {
    label: "Compliance",
    items: [
      { href: "/financeiro", label: "Financeiro", icon: <IconDollar /> },
      { href: "/fiscal/documentos", label: "Fiscal", icon: <IconDoc /> },
      { href: "/licitacoes", label: "Licitações", icon: <IconGavel /> },
      { href: "/juridico", label: "Jurídico", icon: <IconShield /> },
    ],
  },
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
    <div
      className="flex min-h-screen"
      style={{ background: "var(--bg)", color: "var(--fg)" }}
    >
      <aside
        className="sticky top-0 flex h-screen w-60 shrink-0 flex-col px-3 py-5"
        style={{
          background: "var(--sidebar-bg)",
          color: "var(--sidebar-fg)",
        }}
      >
        <Link href="/" className="flex items-center gap-2 px-3">
          <span
            className="flex h-8 w-8 items-center justify-center rounded-lg"
            style={{
              background:
                "linear-gradient(135deg, var(--sidebar-accent), #0f766e)",
              color: "#fff",
              fontWeight: 700,
              fontSize: 13,
              letterSpacing: 0.5,
            }}
          >
            MC
          </span>
          <span>
            <div
              className="display text-[15px] font-bold leading-tight"
              style={{ color: "var(--sidebar-fg-strong)" }}
            >
              Motor Central
            </div>
            <div
              className="text-[10px] font-medium uppercase leading-tight tracking-[0.14em]"
              style={{ color: "#718096" }}
            >
              ZAG · PRIMOR
            </div>
          </span>
        </Link>

        <SidebarNav groups={navGroups} />

        <div
          className="mt-3 rounded-xl p-3"
          style={{
            background: "#111a2e",
            border: "1px solid #1e2a44",
          }}
        >
          <div className="flex items-center gap-2">
            <div
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[12px] font-bold"
              style={{
                background: "var(--sidebar-accent)",
                color: "#0b1220",
              }}
            >
              {(user.nome || user.email || "?").slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div
                className="truncate text-[12.5px] font-semibold"
                style={{ color: "var(--sidebar-fg-strong)" }}
              >
                {user.nome}
              </div>
              <div
                className="truncate text-[11px]"
                style={{ color: "#94a3b8" }}
                title={user.email}
              >
                {user.email}
              </div>
            </div>
          </div>
          <div className="mt-2 flex items-center justify-between">
            <span
              className="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider"
              style={{
                background: "rgba(16, 185, 129, 0.12)",
                color: "var(--sidebar-accent)",
                border: "1px solid rgba(16, 185, 129, 0.3)",
              }}
            >
              {user.role}
            </span>
            <a
              href="/logout"
              className="flex items-center gap-1 text-[11px] font-medium transition hover:text-white"
              style={{ color: "#94a3b8" }}
            >
              <IconLogout width={12} height={12} /> sair
            </a>
          </div>
        </div>
      </aside>
      <main className="flex-1 px-8 py-7">{children}</main>
    </div>
  );
}
