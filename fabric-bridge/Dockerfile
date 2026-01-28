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

# Install AWS CLI for S3 access
RUN apk add --no-cache aws-cli

COPY package.json package-lock.json* ./
# Install only production deps for runtime image
RUN npm ci --omit=dev || npm i --omit=dev

COPY --from=build /app/dist ./dist

# Create directories for identities and certificates
RUN mkdir -p /app/wallets/Org1MSP /app/wallets/Org2MSP /app/certs

# Create entrypoint script to download identities from S3 (optional)
RUN echo '#!/bin/sh' > /usr/local/bin/docker-entrypoint.sh && \
    echo 'set -e' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '' >> /usr/local/bin/docker-entrypoint.sh && \
    echo 'WALLET_SOURCE="${WALLET_SOURCE:-s3}"' >> /usr/local/bin/docker-entrypoint.sh && \
    echo 'if [ "$WALLET_SOURCE" = "s3" ]; then' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  echo "Fetching Fabric identities from S3..."' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  if [ -z "${S3_BUCKET:-}" ]; then' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '    echo "S3_BUCKET is required when WALLET_SOURCE=s3" >&2' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '    exit 1' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  fi' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  aws s3 cp "s3://${S3_BUCKET}/${S3_ORG1_IDENTITY_KEY}" /app/wallets/Org1MSP/svc-org1.id' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  aws s3 cp "s3://${S3_BUCKET}/${S3_ORG1_CERT_KEY}" /app/certs/peer0-org1-tlsca.crt' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  if [ "$ENABLE_ORG2" = "true" ]; then' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '    aws s3 cp "s3://${S3_BUCKET}/${S3_ORG2_IDENTITY_KEY}" /app/wallets/Org2MSP/svc-org2.id' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '    aws s3 cp "s3://${S3_BUCKET}/${S3_ORG2_CERT_KEY}" /app/certs/org2-peer-ca.pem' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  fi' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  chmod 400 /app/wallets/**/*.id' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  chmod 444 /app/certs/*.crt /app/certs/*.pem 2>/dev/null || true' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  echo "Identities retrieved successfully"' >> /usr/local/bin/docker-entrypoint.sh && \
    echo 'else' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '  echo "WALLET_SOURCE=$WALLET_SOURCE; using mounted local wallet and certs"' >> /usr/local/bin/docker-entrypoint.sh && \
    echo 'fi' >> /usr/local/bin/docker-entrypoint.sh && \
    echo '' >> /usr/local/bin/docker-entrypoint.sh && \
    echo 'exec "$@"' >> /usr/local/bin/docker-entrypoint.sh && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 4000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["node", "dist/server.js"]


