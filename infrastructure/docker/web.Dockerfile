# syntax=docker/dockerfile:1.7
# Next.js dashboard as a standalone Node server (output: "standalone" in next.config.ts).
#   docker build -f infrastructure/docker/web.Dockerfile -t flowforge-web .
# NEXT_PUBLIC_API_BASE_URL is inlined at build time. The default (empty) makes the browser call /api/v1 on the
# dashboard's own origin, which is how docker compose serves both behind nginx.

FROM node:24-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

FROM node:24-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=""
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./
RUN npm run build

FROM node:24-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S -g 10001 flowforge && adduser -S -u 10001 -G flowforge flowforge
COPY --from=build --chown=flowforge:flowforge /app/.next/standalone ./
COPY --from=build --chown=flowforge:flowforge /app/.next/static ./.next/static
COPY --from=build --chown=flowforge:flowforge /app/public ./public
USER flowforge
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
CMD ["node", "server.js"]
