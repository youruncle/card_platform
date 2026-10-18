FROM node:22-alpine AS dependencies
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@11.25.0 --activate
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:22-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_SITE_URL
ARG API_INTERNAL_URL=http://api:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    API_INTERNAL_URL=$API_INTERNAL_URL
COPY --from=dependencies /app/node_modules ./node_modules
COPY apps/web .
COPY packages /packages
RUN ./node_modules/.bin/next build

FROM node:22-alpine AS runtime
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup --system --gid 10001 cardplatform && \
    adduser --system --uid 10001 --ingroup cardplatform cardplatform
COPY --from=builder --chown=cardplatform:cardplatform /app/public ./public
COPY --from=builder --chown=cardplatform:cardplatform /app/.next/standalone ./
COPY --from=builder --chown=cardplatform:cardplatform /app/.next/static ./.next/static
USER cardplatform
EXPOSE 3000
CMD ["node", "server.js"]
