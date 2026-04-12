FROM node:20-alpine AS build

WORKDIR /app

COPY apps/studio/package.json apps/studio/package-lock.json ./
RUN npm ci

COPY apps/studio/ ./
RUN npm run build

FROM nginx:1.27-alpine

ENV PORT=80
ENV STUDIO_BACKEND_UPSTREAM=studio-backend:8000

COPY apps/studio/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
