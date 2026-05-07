FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# `output: "standalone"` em next.config.ts emite uma copia minimal da
# app + node_modules em `.next/standalone/`. O runner abaixo so leva
# isso + `.next/static` + `public` -- nao copia o node_modules cheio
# (que carrega devDeps Playwright/eslint/etc).
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Standalone vem com server.js + node_modules minimal pre-empacotados.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
