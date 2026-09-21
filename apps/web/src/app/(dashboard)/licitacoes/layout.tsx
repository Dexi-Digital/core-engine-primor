/**
 * Layout do módulo de Licitações — só existe para a navegação sobreviver.
 *
 * O `SubNav` vivia dentro de `licitacoes/page.tsx`, então só a tela de
 * Oportunidades tinha abas: clicar em qualquer uma levava para uma
 * página sem navegação nenhuma, sem caminho de volta a não ser o botão
 * do navegador. As sete sub-telas do módulo agora herdam as abas daqui.
 *
 * O `SubNav` marca a aba ativa por prefixo mais longo, então as páginas
 * de detalhe (`/licitacoes/123`, `/licitacoes/captacao/123`) acendem a
 * aba do seu pai em vez de nenhuma.
 */
import { SubNav, type SubNavItem } from "@/components/ui/sub-nav";

const LICITACOES_SUBNAV: SubNavItem[] = [
  { href: "/licitacoes", label: "Oportunidades" },
  { href: "/licitacoes/captacao", label: "Captação" },
  { href: "/licitacoes/triagem", label: "Triagem" },
  { href: "/licitacoes/dashboards", label: "Inteligência comercial" },
  { href: "/licitacoes/certidoes", label: "Certidões / atestados" },
  { href: "/licitacoes/boletins", label: "Boletins" },
];

export default function LicitacoesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <SubNav items={LICITACOES_SUBNAV} />
      {children}
    </div>
  );
}
