/**
 * Layout do módulo Manutenção & Frota — as abas valem em todas as
 * sub-telas (veículos, planos, custos, partes diárias), não só na
 * visão geral. Mesmo motivo do layout de Licitações.
 */
import { SubNav, type SubNavItem } from "@/components/ui/sub-nav";

const MANUTENCAO_SUBNAV: SubNavItem[] = [
  { href: "/manutencao", label: "Visão geral" },
  { href: "/manutencao/veiculos", label: "Veículos" },
  { href: "/manutencao/planos", label: "Planos de manutenção" },
  { href: "/manutencao/custos", label: "Custo por equipamento" },
  { href: "/manutencao/partes-diarias", label: "Partes diárias" },
];

export default function ManutencaoLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <SubNav items={MANUTENCAO_SUBNAV} />
      {children}
    </div>
  );
}
