"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

type Item = {
  href: string;
  label: string;
  icon: ReactNode;
};

type Group = {
  label: string;
  items: Item[];
};

export function SidebarNav({ groups }: { groups: Group[] }) {
  const path = usePathname() ?? "";
  return (
    <nav className="mt-6 flex-1 overflow-y-auto pr-1">
      {groups.map((g) => (
        <div key={g.label} className="mb-5">
          <div
            className="mb-2 px-3 text-[10px] font-bold uppercase tracking-[0.14em]"
            style={{ color: "#5b6b86" }}
          >
            {g.label}
          </div>
          <ul className="space-y-0.5">
            {g.items.map((item) => {
              const active =
                item.href === "/"
                  ? path === "/"
                  : path === item.href || path.startsWith(item.href + "/");
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className="group flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] font-medium transition"
                    style={{
                      color: active
                        ? "var(--sidebar-fg-strong)"
                        : "var(--sidebar-fg)",
                      background: active
                        ? "var(--sidebar-active)"
                        : "transparent",
                      borderLeft: active
                        ? `2px solid var(--sidebar-accent)`
                        : "2px solid transparent",
                      paddingLeft: active ? "10px" : "12px",
                    }}
                    onMouseEnter={(e) => {
                      if (!active)
                        e.currentTarget.style.background =
                          "var(--sidebar-hover)";
                    }}
                    onMouseLeave={(e) => {
                      if (!active)
                        e.currentTarget.style.background = "transparent";
                    }}
                  >
                    <span
                      className="flex h-4 w-4 items-center justify-center"
                      style={{
                        color: active
                          ? "var(--sidebar-accent)"
                          : "var(--sidebar-fg)",
                      }}
                    >
                      {item.icon}
                    </span>
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
