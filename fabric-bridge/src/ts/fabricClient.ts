import fs from 'fs';
import path from 'path';
import { Wallets, Gateway, GatewayOptions, Network, Contract } from 'fabric-network';
import * as grpc from '@grpc/grpc-js';

type ConnectArgs = {
  walletPath: string;
  identityLabel: string;
  mspId: string;
  channelName: string;
  chaincodeName: string;
  peerEndpoint: string;
  tlsCertPath: string; // optional if using insecure
};

export async function connectGateway(args: ConnectArgs) {
  const wallet = await Wallets.newFileSystemWallet(args.walletPath);
  const identity = await wallet.get(args.identityLabel);
  if (!identity) {
    throw new Error(`Identity '${args.identityLabel}' not found in wallet at ${args.walletPath}`);
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
    args = [JSON.stringify(payload.data)];
  } else {
    fn = 'UpdateArtifact';
    args = [payload.artifactId, JSON.stringify(payload.patch || {})];
  }

  const transaction = conn.contract.createTransaction(fn);
  const txId = transaction.getTransactionId();
  await transaction.submit(...args);
  return { txId, committedAt: now };
}


