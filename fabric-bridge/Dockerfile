FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS build
WORKDIR /app
RUN apk add --no-cache python3 make g++

COPY package.json package-lock.json .npmrc ./
COPY scripts/security/check_npm_supply_chain.py ./scripts/security/check_npm_supply_chain.py
COPY security/npm-malware-blocklist.csv security/npm-lifecycle-allowlist.json ./security/
# Install ALL deps (including dev) for build stage so tsc is available
RUN python3 scripts/security/check_npm_supply_chain.py --repo . --offline-reviewed --skip-installed \
    && npm ci --ignore-scripts --no-audit --fund=false \
    && npm audit signatures \
    && npm rebuild pkcs11js@2.1.7 protobufjs@7.6.5 --ignore-scripts=false \
    && python3 scripts/security/check_npm_supply_chain.py --repo . --offline-reviewed

COPY tsconfig.json ./
COPY src ./src

RUN npm run build && npm prune --omit=dev --ignore-scripts

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS runtime
WORKDIR /app
ENV NODE_ENV=production

# Install AWS CLI for S3 access
RUN apk add --no-cache aws-cli

COPY package.json package-lock.json ./
COPY --from=build /app/node_modules ./node_modules

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
