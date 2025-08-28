import fs from 'fs';
import path from 'path';
import * as grpc from '@grpc/grpc-js';
import { connect, signers, Contract, Network, Gateway } from '@hyperledger/fabric-gateway';
import { createPrivateKey } from 'node:crypto';

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
  discoveryEnabled?: boolean; // retained for compatibility (not used by gateway connect)
  discoveryAsLocalhost?: boolean; // retained for compatibility (not used by gateway connect)
};

export async function connectGateway(args: ConnectArgs) {
  // Load identity from wallet-like JSON file(s)
  const candidates: string[] = [];
  if (args.identityFilePath) candidates.push(args.identityFilePath);
  candidates.push(path.join(args.walletPath, args.mspId, `${args.identityLabel}.id`));
  candidates.push(path.join(args.walletPath, `${args.identityLabel}.id`));

  let certificatePem = '';
  let privateKeyPem = '';
  for (const p of candidates) {
    try {
      if (!fs.existsSync(p)) continue;
      const raw = fs.readFileSync(p, 'utf8');
      try {
        const j = JSON.parse(raw);
        certificatePem = j.certificate || j.cert || (j.credentials && (j.credentials.certificate || j.credentials.cert)) || '';
        privateKeyPem = j.privateKey || j.key || (j.credentials && (j.credentials.privateKey || j.credentials.key)) || '';
      } catch {
        const certMatch = raw.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/);
        const keyMatch = raw.match(/-----BEGIN (?:PRIVATE KEY|EC PRIVATE KEY)-----[\s\S]*?-----END (?:PRIVATE KEY|EC PRIVATE KEY)-----/);
        certificatePem = certMatch ? certMatch[0] : '';
        privateKeyPem = keyMatch ? keyMatch[0] : '';
      }
      if (certificatePem && privateKeyPem) break;
    } catch {}
  }
  if (!certificatePem || !privateKeyPem) {
    throw new Error(`Identity '${args.identityLabel}' not found or invalid in ${args.walletPath}`);
  }

  // Build TLS gRPC client
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

  // Gateway identity and signer
  const identity = { mspId: args.mspId, credentials: Buffer.from(certificatePem) };
  const privateKey = createPrivateKey(privateKeyPem);
  const signer = signers.newPrivateKeySigner(privateKey);

  const gateway = connect({
    client: client as any,
    identity,
    signer,
    evaluateOptions: () => ({ deadline: Date.now() + 5000 }),
    endorseOptions: () => ({ deadline: Date.now() + 15000 }),
    submitOptions: () => ({ deadline: Date.now() + 5000 }),
    commitStatusOptions: () => ({ deadline: Date.now() + 60000 })
  });

  const network: Network = gateway.getNetwork(args.channelName) as any;
  const contract: Contract = network.getContract(args.chaincodeName) as any;

  return {
    gateway,
    network,
    contract,
    chaincodeName: args.chaincodeName,
    close: () => gateway.close()
  };
}

type SubmitOptions = {
  submitFn?: string;
  updateFn?: string;
  submitArgsMode?: 'id+data' | 'json' | 'data-only';
  updateArgsMode?: 'id+patch' | 'json';
  endorsingOrgs?: string[]; // retained; gateway may ignore if not supported in this client
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

  // Build, endorse, and submit using Fabric Gateway
  try {
    const proposal = (conn.contract as any).newProposal(fn, { arguments: args });
    const endorsed = await (proposal as any).endorse();
    const txId = (endorsed as any).transactionId as string;
    const commit = await (endorsed as any).submit();
    const status = await (commit as any).getStatus();
    if (status && typeof status.code === 'number' && status.code !== 0) {
      throw new Error(`Commit failed with status code ${status.code}`);
    }
    return { txId, committedAt: now };
  } catch (e: any) {
    const msg = e?.message || String(e);
    throw new Error(`Fabric submit failed for ${fn}: ${msg}`);
  }
}


