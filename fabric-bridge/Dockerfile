FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./
# Install ALL deps (including dev) for build stage so tsc is available
RUN npm ci || npm i

COPY tsconfig.json ./
COPY src ./src

RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production

COPY package.json package-lock.json* ./
# Install only production deps for runtime image
RUN npm ci --omit=dev || npm i --omit=dev

COPY --from=build /app/dist ./dist

EXPOSE 4000
CMD ["node", "dist/server.js"]


