"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export type SubNavItem = {
  href: string;
  label: string;
};

/**
 * Navegacao entre as sub-paginas de um modulo.
 *
 * Existe porque os modulos vinham empilhando as sub-paginas como botoes
 * soltos no canto do header: cinco itens de mesmo peso visual competindo
 * com o titulo, sem indicar onde voce esta nem por onde comecar. Aqui
 * viram abas, com o item ativo marcado -- mesma logica de prefixo mais
 * longo do `SidebarNav`, para uma rota filha nao acender duas abas.
 *
 * Rola na horizontal em tela estreita em vez de quebrar em bloco.
 */
export function SubNav({ items }: { items: SubNavItem[] }) {
  const path = usePathname() ?? "";
  const activeHref =
    items
      .filter((it) => path === it.href || path.startsWith(it.href + "/"))
      .map((it) => it.href)
      .sort((a, b) => b.length - a.length)[0] ?? null;

  return (
    <nav
      className="-mt-2 mb-6 flex gap-1 overflow-x-auto"
      style={{ borderBottom: "1px solid var(--border)" }}
      aria-label="Seções do módulo"
    >
      {items.map((item) => {
        const active = item.href === activeHref;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className="whitespace-nowrap px-3 py-2.5 text-sm font-medium transition"
            style={{
              color: active ? "var(--accent)" : "var(--fg-muted)",
              borderBottom: active
                ? "2px solid var(--accent)"
                : "2px solid transparent",
              marginBottom: "-1px",
            }}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
