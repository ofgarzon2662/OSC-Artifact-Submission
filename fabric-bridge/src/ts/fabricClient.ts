import fs from 'node:fs';
import path from 'node:path';
import { createPrivateKey } from 'node:crypto';
import * as grpc from '@grpc/grpc-js';
import {
  connect,
  Contract,
  Gateway,
  Network,
  signers
} from '@hyperledger/fabric-gateway';

export type FabricConnectionArgs = {
  certificatePath: string;
  privateKeyPath: string;
  mspId: string;
  channelName: string;
  chaincodeName: string;
  peerEndpoint: string;
  tlsCertPath: string;
  tlsServerName: string;
};

export type FabricConnection = {
  gateway: Gateway;
  network: Network;
  contract: Contract;
  close: () => void;
};

export type ProvenanceAssetType = 'artifact' | 'workflow';
export type ProvenanceAction = 'create' | 'update';

export type FabricDiagnostic = {
  address?: string;
  mspId?: string;
  message?: string;
};

export class FabricOperationError extends Error {
  readonly details: FabricDiagnostic[];
  readonly cause: unknown;

  constructor(operation: string, error: unknown) {
    const source = error as any;
    const message = source?.message || String(error);
    super(`Fabric ${operation} failed: ${message}`);
    this.name = 'FabricOperationError';
    this.cause = error;
    this.details = fabricDiagnostics(source);
  }
}

const contractFunctions: Record<
  ProvenanceAssetType,
  Record<ProvenanceAction, string>
> = {
  artifact: { create: 'CreateArtifact', update: 'UpdateArtifact' },
  workflow: { create: 'CreateWorkflow', update: 'UpdateWorkflow' }
};

const historyFunctions: Record<ProvenanceAssetType, string> = {
  artifact: 'GetArtifactHistory',
  workflow: 'GetWorkflowHistory'
};

function positiveInteger(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value || '', 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function boundedText(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0
    ? value.slice(0, 2048)
    : undefined;
}

function fabricDiagnostics(error: any): FabricDiagnostic[] {
  const rawDetails = Array.isArray(error?.details)
    ? error.details
    : [error?.details, error?.cause?.details].filter(Boolean);
  return rawDetails
    .slice(0, 20)
    .map((detail: any) => {
      if (typeof detail === 'string') return { message: boundedText(detail) };
      return {
        address: boundedText(detail?.address),
        mspId: boundedText(detail?.mspId),
        message: boundedText(detail?.message)
      };
    })
    .filter((detail: FabricDiagnostic) => Object.values(detail).some(Boolean));
}

function readRequiredFile(filePath: string, description: string): Buffer {
  if (!filePath.trim()) {
    throw new Error(`${description} path is required`);
  }
  const value = fs.readFileSync(path.resolve(filePath));
  if (value.length === 0) {
    throw new Error(`${description} is empty`);
  }
  return value;
}

export async function connectGateway(
  args: FabricConnectionArgs
): Promise<FabricConnection> {
  if (!args.peerEndpoint.trim()) throw new Error('Fabric peer endpoint is required');
  if (!args.tlsServerName.trim()) throw new Error('Fabric TLS server name is required');

  const certificate = readRequiredFile(args.certificatePath, 'Fabric certificate');
  const privateKeyPem = readRequiredFile(args.privateKeyPath, 'Fabric private key');
  const tlsRootCertificate = readRequiredFile(
    args.tlsCertPath,
    'Fabric peer TLS root certificate'
  );

  const channelOptions: grpc.ChannelOptions = {
    'grpc.ssl_target_name_override': args.tlsServerName,
    'grpc.default_authority': args.tlsServerName,
    'grpc.max_receive_message_length': positiveInteger(
      process.env.GRPC_MAX_RECV_BYTES,
      64 * 1024 * 1024
    ),
    'grpc.max_send_message_length': positiveInteger(
      process.env.GRPC_MAX_SEND_BYTES,
      16 * 1024 * 1024
    )
  };
  const client = new grpc.Client(
    args.peerEndpoint,
    grpc.credentials.createSsl(tlsRootCertificate),
    channelOptions
  );

  const identity = { mspId: args.mspId, credentials: certificate };
  const signer = signers.newPrivateKeySigner(createPrivateKey(privateKeyPem));
  const gateway = connect({
    client,
    identity,
    signer,
    evaluateOptions: () => ({
      deadline: Date.now() + positiveInteger(process.env.EVALUATE_DEADLINE_MS, 60_000)
    }),
    endorseOptions: () => ({
      deadline: Date.now() + positiveInteger(process.env.ENDORSE_DEADLINE_MS, 15_000)
    }),
    submitOptions: () => ({
      deadline: Date.now() + positiveInteger(process.env.SUBMIT_DEADLINE_MS, 5_000)
    }),
    commitStatusOptions: () => ({
      deadline:
        Date.now() + positiveInteger(process.env.COMMIT_STATUS_DEADLINE_MS, 60_000)
    })
  });

  const network = gateway.getNetwork(args.channelName);
  const contract = network.getContract(args.chaincodeName, 'ProvenanceContract');
  return {
    gateway,
    network,
    contract,
    close: () => {
      gateway.close();
      client.close();
    }
  };
}

export async function submitProvenanceTransaction(
  connection: FabricConnection,
  assetType: ProvenanceAssetType,
  action: ProvenanceAction,
  assetId: string,
  payload: Record<string, unknown>,
  requestMetadata: Record<string, unknown>
): Promise<{ txId: string; committedAt: string; result: unknown }> {
  const functionName = contractFunctions[assetType][action];
  try {
    const proposal = connection.contract.newProposal(functionName, {
      arguments: [assetId, JSON.stringify(payload), JSON.stringify(requestMetadata)]
    });
    const transactionId = proposal.getTransactionId();
    const endorsed = await proposal.endorse();
    const submitted = await endorsed.submit();
    const status = await submitted.getStatus();
    if (!status.successful) {
      throw new Error(
        `Fabric transaction ${status.transactionId} committed with status ${status.code}`
      );
    }
    const resultText = Buffer.from(endorsed.getResult()).toString('utf8');
    let result: unknown = resultText;
    try {
      result = JSON.parse(resultText);
    } catch {
      // Chaincode may intentionally return a non-JSON scalar.
    }
    return {
      txId: transactionId,
      committedAt: new Date().toISOString(),
      result
    };
  } catch (error: unknown) {
    throw new FabricOperationError(`submission ${functionName}`, error);
  }
}

export async function evaluateHistory(
  connection: FabricConnection,
  assetType: ProvenanceAssetType,
  assetId: string
): Promise<unknown> {
  const functionName = historyFunctions[assetType];
  try {
    const result = await connection.contract.evaluateTransaction(
      functionName,
      assetId
    );
    return JSON.parse(Buffer.from(result).toString('utf8'));
  } catch (error: unknown) {
    throw new FabricOperationError(`evaluation ${functionName}`, error);
  }
}
