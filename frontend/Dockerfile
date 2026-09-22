# syntax=docker/dockerfile:1
# SPA estatica: build com Node 22 + pnpm, servida por nginx stable.

# ---- build ------------------------------------------------------------------
FROM node:26-alpine AS build
ENV PNPM_HOME=/pnpm \
    CI=true
RUN corepack enable
WORKDIR /app

# Deps primeiro (camada cacheada enquanto o lockfile nao mudar).
# A versao do pnpm vem do campo packageManager do package.json (corepack).
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# ---- runtime ----------------------------------------------------------------
FROM nginx:1.30-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/healthz || exit 1
