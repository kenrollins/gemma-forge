# Next.js 16 UI — served as a standalone Node server.
#
# Uses Next's built-in `output: "standalone"` tracer (see next.config.ts)
# to produce a minimal runtime bundle that does NOT carry the full
# node_modules tree. Multi-stage build: a `deps` stage for npm install,
# a `builder` stage for next build, and a tiny `runner` stage that only
# copies the standalone output + public + static assets.
#
# Ref: node_modules/next/dist/docs/01-app/03-api-reference/05-config/
#      01-next-config-js/output.md (bundled Next 16.2.3 docs).

# ---- deps ----
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder ----
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# FORGE_API_URL is consumed by next.config.ts' rewrites() and gets
# serialized into the standalone routes manifest at build time — so it
# must be set here, not in the runner. Compose overrides the default
# via build.args to point at the sibling container (http://forge-api:8080).
ARG FORGE_API_URL=http://localhost:8080
ENV FORGE_API_URL=${FORGE_API_URL}

RUN npm run build

# ---- runner ----
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Non-root user for the runtime. Matches Next's with-docker example.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# Standalone output includes a pruned node_modules and a minimal
# server.js — no `next start` needed. public/ and .next/static/ are
# NOT copied into standalone automatically; copy them explicitly.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
