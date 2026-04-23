import Link from "next/link";
import type { ReactNode } from "react";

const nav = [
  { href: "/rh", label: "RH / DP" },
  { href: "/manutencao", label: "Manutenção" },
  { href: "/financeiro", label: "Financeiro" },
  { href: "/licitacoes", label: "Licitações" },
  { href: "/juridico", label: "Jurídico" },
];

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r border-slate-200 bg-white px-4 py-6">
        <Link href="/" className="block text-sm font-bold uppercase tracking-widest">
          Motor Central
        </Link>
        <nav className="mt-8 flex flex-col gap-1 text-sm">
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
      </aside>
      <main className="flex-1 px-8 py-8">{children}</main>
    </div>
  );
}
