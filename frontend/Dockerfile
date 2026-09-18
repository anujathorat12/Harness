# Admin/operator frontend. Multi-stage: build the static bundle with Node,
# then serve it with a plain nginx -- no Node runtime needed in production,
# and no reverse proxy here (the built app calls the backend's own REST API
# directly over CORS -- see harness/main.py's CORSMiddleware -- so nginx's
# only job is serving static files).
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
# Baked in at build time -- change this build arg if the backend isn't at
# the default localhost:8000 (e.g. a real deployment with a real hostname).
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
