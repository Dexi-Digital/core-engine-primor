/**
 * Abas do modulo RH -- uma lista so.
 *
 * Estava duplicada em `/rh` e `/rh/ponto`, com um comentario em cada
 * pedindo para manter as duas em sincronia. Um pedido desses so e
 * necessario porque o codigo permite a divergencia; ao entrar a
 * terceira aba (Admissões), extrair virou o menor caminho.
 */
import type { SubNavItem } from "@/components/ui/sub-nav";

export const RH_SUBNAV: SubNavItem[] = [
  { href: "/rh", label: "Visão geral" },
  { href: "/rh/admissoes", label: "Admissões" },
  { href: "/rh/funcionarios", label: "Funcionários" },
  { href: "/rh/equipe-administrativa", label: "Equipe administrativa" },
  { href: "/rh/afastamentos", label: "Afastamentos" },
  { href: "/rh/ponto", label: "Ponto" },
];
