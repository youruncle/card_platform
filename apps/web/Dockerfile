FROM node:22-alpine AS dependencies
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:22-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_SITE_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
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
