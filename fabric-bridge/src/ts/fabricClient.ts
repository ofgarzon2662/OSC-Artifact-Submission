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
  sslOverride?: string; // optional TLS server name override (SNI)
  discoveryEnabled?: boolean;
  discoveryAsLocalhost?: boolean;
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
  const channelOptions: Record<string, any> = {};
  if (args.sslOverride) {
    channelOptions['grpc.ssl_target_name_override'] = args.sslOverride;
    channelOptions['grpc.default_authority'] = args.sslOverride;
  }
  const client = new (grpc as any).Client(args.peerEndpoint, creds, channelOptions);

  const gateway = new Gateway();
  await gateway.connect(client as any, {
    wallet,
    identity: args.identityLabel,
    discovery: {
      enabled: args.discoveryEnabled ?? false,
      asLocalhost: args.discoveryAsLocalhost ?? true
    }
  } as GatewayOptions);

  const network: Network = await gateway.getNetwork(args.channelName);
  const contract: Contract = network.getContract(args.chaincodeName);

  return {
    gateway,
    network,
    contract,
    chaincodeName: args.chaincodeName,
    close: () => gateway.disconnect()
  };
}

type SubmitOptions = {
  submitFn?: string;
  updateFn?: string;
  submitArgsMode?: 'id+data' | 'json' | 'data-only';
  updateArgsMode?: 'id+patch' | 'json';
  endorsingOrgs?: string[];
};

export async function submitTxIfReal(
  conn: any,
  action: 'submit' | 'update',
  payload: any,
  options?: SubmitOptions
) {
  const now = new Date().toISOString();
  const submitFn = options?.submitFn || 'SubmitArtifact';
  const updateFn = options?.updateFn || 'UpdateArtifact';
  const submitArgsMode = options?.submitArgsMode || 'id+data';
  const updateArgsMode = options?.updateArgsMode || 'id+patch';

  let fn = '';
  let args: string[] = [];
  if (action === 'submit') {
    fn = submitFn;
    if (submitArgsMode === 'json') {
      args = [JSON.stringify({ artifactId: payload.artifactId, data: payload.data })];
    } else if (submitArgsMode === 'data-only') {
      // Inject id field expected by chaincode CreateArtifact
      const merged = { ...(payload.data || {}), id: payload.artifactId };
      args = [JSON.stringify(merged)];
    } else {
      args = [payload.artifactId, JSON.stringify(payload.data)];
    }
  } else {
    fn = updateFn;
    if (updateArgsMode === 'json') {
      args = [JSON.stringify({ artifactId: payload.artifactId, patch: payload.patch || {} })];
    } else {
      args = [payload.artifactId, JSON.stringify(payload.patch || {})];
    }
  }

  // Help discovery by declaring interest in this chaincode
  try {
    if (conn?.contract && conn?.chaincodeName && (conn as any).contract.addDiscoveryInterest) {
      (conn as any).contract.addDiscoveryInterest({ name: conn.chaincodeName });
    }
  } catch {}

  const transaction = conn.contract.createTransaction(fn);
  if (options?.endorsingOrgs && options.endorsingOrgs.length > 0 && (transaction as any).setEndorsingOrganizations) {
    (transaction as any).setEndorsingOrganizations(...options.endorsingOrgs);
  }
  const txId = transaction.getTransactionId();
  try {
    await transaction.submit(...args);
  } catch (e: any) {
    const responses = Array.isArray(e?.responses)
      ? e.responses.map((r: any) => r?.response?.message || r?.message || r)
      : undefined;
    const msg = responses && responses.length > 0 ? responses : (e?.message || String(e));
    throw new Error(`Fabric submit failed for ${fn}: ${JSON.stringify(msg)}`);
  }
  return { txId, committedAt: now };
}


