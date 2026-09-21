/**
 * Layout do módulo Financeiro & Contratos — abas em todas as sub-telas.
 */
import { SubNav, type SubNavItem } from "@/components/ui/sub-nav";

const FINANCEIRO_SUBNAV: SubNavItem[] = [
  { href: "/financeiro", label: "Visão geral" },
  { href: "/financeiro/contratos", label: "Contratos" },
];

export default function FinanceiroLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <SubNav items={FINANCEIRO_SUBNAV} />
      {children}
    </div>
  );
}
