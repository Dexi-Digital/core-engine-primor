import type { ReactNode } from "react";

/**
 * Layout do PWA mobile (D5). Diferente de `(dashboard)`:
 *   - sem sidebar de navegacao (espaco escasso em telefone)
 *   - injeta <link rel="manifest"> para o browser sugerir instalar
 *
 * Auth e gerenciada pelo `middleware.ts` -- toda rota /m/* tras o
 * cookie HttpOnly automaticamente, ou redireciona pra /login.
 */
export default function MobileLayout({ children }: { children: ReactNode }) {
  return (
    <>
      {/* Next puxa esse link no <head> automaticamente */}
      <link rel="manifest" href="/manifest.webmanifest" />
      <link rel="apple-touch-icon" href="/m/icons/icon-192.svg" />
      {children}
    </>
  );
}
