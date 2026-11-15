# Imagen de producción del frontend: compila con Vite y sirve los estáticos con Caddy, que además
# reenvía /api/* a la API (mismo papel que el proxy de Vite en desarrollo: sin CORS).
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .
RUN npx tsc --noEmit -p tsconfig.json && npx vite build


FROM caddy:2-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv

# API_URL: URL interna de la API, p. ej. http://api.railway.internal:8000 (ver Caddyfile).
ENV PORT=8080
EXPOSE 8080
