FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite inlines the API URL at build time (see vite.config.ts), so it is a build arg, not runtime
# env: the published image is baked with http://localhost:8002 and a different backend needs a
# rebuild with --build-arg VITE_API_URL=...
ARG VITE_API_URL=http://localhost:8002
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:1.29-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 3002
