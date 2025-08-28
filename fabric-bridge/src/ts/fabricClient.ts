import fs from 'fs';
import path from 'path';
import { Wallets, Gateway, GatewayOptions, Network, Contract, X509Identity } from 'fabric-network';
import * as grpc from '@grpc/grpc-js';

type ConnectArgs = {
  walletPath: string;
  identityLabel: string;
  mspId: string;
  channelName: string;
  chaincodeName: string;
  peerEndpoint: string;
  tlsCertPath: string; // optional if using insecure
  identityFilePath?: string; // optional direct path to identity file
};

export async function connectGateway(args: ConnectArgs) {
  const primaryWallet = await Wallets.newFileSystemWallet(args.walletPath);
  let wallet = primaryWallet;
  let identity = await primaryWallet.get(args.identityLabel);

  const ensureWritableWallet = async () => {
    const walletStorePath = process.env.WALLET_STORE_PATH || '/tmp/wallet';
    fs.mkdirSync(walletStorePath, { recursive: true });
    if (!wallet || (wallet as any).storePath !== walletStorePath) {
      wallet = await Wallets.newFileSystemWallet(walletStorePath);
    }
    return wallet;
  };

  const rehydrateIntoWritableWallet = async (src: any) => {
    const cert = src?.credentials?.certificate || src?.certificate || src?.cert;
    const key = src?.credentials?.privateKey || src?.privateKey || src?.key;
    if (cert && key) {
      const x509: X509Identity = {
        credentials: { certificate: cert, privateKey: key },
        mspId: args.mspId,
        type: 'X.509'
      };
      await ensureWritableWallet();
      await wallet.put(args.identityLabel, { ...(x509 as any), version: 1 } as any);
      return await wallet.get(args.identityLabel);
    }
    return undefined;
  };

  // If an identity exists but is not a proper wallet entry (e.g., missing version),
  // rehydrate it into a writable wallet with a proper structure.
  if (identity && !(identity as any).version) {
    identity = await rehydrateIntoWritableWallet(identity);
  }
  if (!identity) {
    // Auto-import from identity file structure for automation
    const candidates: string[] = [];
    if (args.identityFilePath) candidates.push(args.identityFilePath);
    candidates.push(path.join(args.walletPath, args.mspId, `${args.identityLabel}.id`));
    candidates.push(path.join(args.walletPath, `${args.identityLabel}.id`));

    let imported = false;
    for (const p of candidates) {
      try {
        if (fs.existsSync(p)) {
          const raw = fs.readFileSync(p, 'utf8');
          let cert = '';
          let key = '';
          // Try JSON first: { certificate, privateKey } OR { cert, key }
          try {
            const j = JSON.parse(raw);
            // Support multiple JSON shapes:
            // { certificate, privateKey } OR { cert, key } OR { credentials: { certificate, privateKey } }
            cert = j.certificate || j.cert || (j.credentials && (j.credentials.certificate || j.credentials.cert)) || '';
            key = j.privateKey || j.key || (j.credentials && (j.credentials.privateKey || j.credentials.key)) || '';
          } catch {
            // Not JSON: attempt to split PEM blocks (very naive fallback)
            // Expect both CERT and KEY present concatenated
            const certMatch = raw.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/);
            const keyMatch = raw.match(/-----BEGIN (?:PRIVATE KEY|EC PRIVATE KEY)-----[\s\S]*?-----END (?:PRIVATE KEY|EC PRIVATE KEY)-----/);
            cert = certMatch ? certMatch[0] : '';
            key = keyMatch ? keyMatch[0] : '';
          }
          if (!cert || !key) {
            continue;
          }
          const x509: X509Identity = {
            credentials: { certificate: cert, privateKey: key },
            mspId: args.mspId,
            type: 'X.509'
          };
          // Some wallet stores expect an explicit version marker
          // Store imported identity in a writable internal wallet to avoid read-only mounts
          await ensureWritableWallet();
          await wallet.put(args.identityLabel, { ...(x509 as any), version: 1 } as any);
          identity = await wallet.get(args.identityLabel);
          imported = true;
          break;
        }
      } catch (e) {
        // continue trying next candidate
      }
    }
    if (!imported || !identity) {
      throw new Error(`Identity '${args.identityLabel}' not found in wallet at ${args.walletPath} and no importable identity file located`);
    }
  }

  // gRPC connection
  let creds: grpc.ChannelCredentials;
  if (args.tlsCertPath) {
    const tlsCert = fs.readFileSync(path.resolve(args.tlsCertPath));
    creds = grpc.credentials.createSsl(tlsCert);
  } else {
    creds = grpc.credentials.createInsecure();
  }
  const client = new (grpc as any).Client(args.peerEndpoint, creds);

  const gateway = new Gateway();
  await gateway.connect(client as any, {
    wallet,
    identity: args.identityLabel,
    discovery: { enabled: false, asLocalhost: true }
  } as GatewayOptions);

  const network: Network = await gateway.getNetwork(args.channelName);
  const contract: Contract = network.getContract(args.chaincodeName);

  return {
    gateway,
    network,
    contract,
    close: () => gateway.disconnect()
  };
}

export async function submitTxIfReal(conn: any, action: 'submit'|'update', payload: any) {
  const now = new Date().toISOString();
  let fn = '';
  let args: string[] = [];
  if (action === 'submit') {
    fn = 'SubmitArtifact';
    // Pass both artifactId and data to match typical chaincode signatures
    args = [payload.artifactId, JSON.stringify(payload.data)];
  } else {
    fn = 'UpdateArtifact';
    args = [payload.artifactId, JSON.stringify(payload.patch || {})];
  }

  const transaction = conn.contract.createTransaction(fn);
  const txId = transaction.getTransactionId();
  try {
    await transaction.submit(...args);
  } catch (e: any) {
    const details = e?.details || e?.responses || e?.message || e;
    throw new Error(`Fabric submit failed for ${fn}: ${JSON.stringify(details)}`);
  }
  return { txId, committedAt: now };
}


