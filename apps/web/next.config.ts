import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output reduz a imagem Docker em ~5x (de ~1GB pra ~200MB):
  // o build emite `apps/web/.next/standalone/` com server.js + minimal
  // node_modules, e o Dockerfile final stage so copia esse subset +
  // `.next/static` + `public/`. Sem isso, COPY do node_modules inteiro
  // (incluindo devDeps) sobe junto.
  output: "standalone",
};

export default nextConfig;
