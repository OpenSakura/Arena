# syntax=docker/dockerfile:1

# --- Dependencies stage ---
FROM node:20-slim AS deps

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

# --- Build stage ---
FROM node:20-slim AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Build-time env vars (override at build time as needed)
ARG NEXT_PUBLIC_BACKEND_URL=""
ARG NEXT_PUBLIC_TURNSTILE_SITE_KEY=""

ENV NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL
ENV NEXT_PUBLIC_TURNSTILE_SITE_KEY=$NEXT_PUBLIC_TURNSTILE_SITE_KEY
ENV NEXT_PRIVATE_STANDALONE=true

RUN npm run build

# --- Runtime stage ---
FROM node:20-slim AS runtime

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --no-create-home appuser

WORKDIR /app

# Copy Next.js standalone output
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

USER appuser

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
