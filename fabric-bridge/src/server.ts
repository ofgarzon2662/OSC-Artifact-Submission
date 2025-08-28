import express, { Request, Response } from 'express';
import morgan from 'morgan';
import dotenv from 'dotenv';
import { StatusCodes } from 'http-status-codes';
import Joi from 'joi';
import { v4 as uuidv4 } from 'uuid';
import { connectGateway, submitTxIfReal } from './ts/fabricClient.js';

dotenv.config();

// Configuration
const PORT = parseInt(process.env.PORT || '4000', 10);
const FABRIC_CHANNEL = process.env.FABRIC_CHANNEL || 'mychannel';
const FABRIC_CHAINCODE = process.env.FABRIC_CHAINCODE || 'artifacts';
const FABRIC_PEER = process.env.FABRIC_PEER || 'localhost:7051';
const WALLET_PATH = process.env.WALLET_PATH || '/wallets/Org1MSP/svc-org1.id';
const MSP_ID = process.env.MSP_ID || 'Org1MSP';
const IDENTITY_LABEL = process.env.IDENTITY_LABEL || 'svc-org1';
const FABRIC_REAL_MODE = /^true$/i.test(process.env.FABRIC_REAL_MODE || 'false');
const PEER_ENDPOINT = process.env.PEER_ENDPOINT || 'localhost:7051';
const TLS_CERT_PATH = process.env.TLS_CERT_PATH || '';

// Minimal request schemas
const artifactDataSchema = Joi.object({
  title: Joi.string().min(1).required(),
  description: Joi.string().min(50).required(),
  manifest: Joi.any().required(),
  keywords: Joi.array().items(Joi.string()).optional(),
  links: Joi.array().items(Joi.any()).optional(),
  dois: Joi.array().items(Joi.string()).optional(),
  fundingAgencies: Joi.array().items(Joi.any()).optional(),
  acknowledgements: Joi.string().optional(),
  footprint: Joi.string().pattern(/^[a-fA-F0-9]{64}$/).required()
}).required();

const submitSchema = Joi.object({
  artifactId: Joi.string().uuid().required(),
  data: artifactDataSchema,
  correlationId: Joi.string().optional()
});

const updateSchema = Joi.object({
  artifactId: Joi.string().uuid().required(),
  patch: Joi.object().required(),
  correlationId: Joi.string().optional()
});

// Fabric adapter stub (replace with real Fabric Gateway SDK integration)
async function submitToFabric(action: 'submit'|'update', payload: any) {
  // Simulate I/O latency to peer
  await new Promise((res) => setTimeout(res, 300));

  // In real implementation:
  // - Build a Gateway connection using wallet at WALLET_PATH with MSP_ID & IDENTITY_LABEL
  // - getNetwork(FABRIC_CHANNEL).getContract(FABRIC_CHAINCODE)
  // - submitTransaction('SubmitArtifact', JSON.stringify(payload))
  // - For update: submitTransaction('UpdateArtifact', ...)

  // In real mode, submit to Fabric
  if (FABRIC_REAL_MODE) {
    const gateway = await connectGateway({
      walletPath: WALLET_PATH,
      identityLabel: IDENTITY_LABEL,
      mspId: MSP_ID,
      channelName: FABRIC_CHANNEL,
      chaincodeName: FABRIC_CHAINCODE,
      peerEndpoint: PEER_ENDPOINT,
      tlsCertPath: TLS_CERT_PATH
    });
    try {
      const { txId, committedAt } = await submitTxIfReal(gateway, action, payload);
      return { txId, committedAt, peer: FABRIC_PEER };
    } finally {
      gateway.close();
    }
  }

  // Simple failure simulation for invalid payloads (defensive guard in mock mode)
  if (action === 'submit') {
    const d = payload?.data;
    const invalid = !d || typeof d.title !== 'string' || !d.title ||
      typeof d.footprint !== 'string' || !/^[a-fA-F0-9]{64}$/.test(d.footprint);
    if (invalid) {
      throw new Error('Fabric validation failed: missing/invalid title or footprint');
    }
  }

  const txId = uuidv4().replace(/-/g, '');
  const committedAt = new Date().toISOString();
  return { txId, committedAt, peer: FABRIC_PEER };
}

const app = express();
app.use(express.json({ limit: '1mb' }));
app.use(morgan('dev'));

app.get('/health', (_req: Request, res: Response) => {
  res.json({
    status: 'healthy',
    service: 'fabric-bridge',
    timestamp: new Date().toISOString(),
    config: {
      channel: FABRIC_CHANNEL,
      chaincode: FABRIC_CHAINCODE,
      peer: FABRIC_PEER,
      walletPath: WALLET_PATH,
      mspId: MSP_ID,
      identityLabel: IDENTITY_LABEL
    }
  });
});

app.post('/submit', async (req: Request, res: Response) => {
  const { error, value } = submitSchema.validate(req.body, { abortEarly: false });
  if (error) {
    return res.status(StatusCodes.BAD_REQUEST).json({ message: 'Validation failed', details: error.details });
  }
  const correlationId = value.correlationId || uuidv4();
  try {
    const result = await submitToFabric('submit', { artifactId: value.artifactId, data: value.data, correlationId });
    return res.status(StatusCodes.OK).json({ success: true, correlationId, ...result });
  } catch (e: any) {
    return res.status(StatusCodes.INTERNAL_SERVER_ERROR).json({ success: false, correlationId, error: String(e?.message || e) });
  }
});

app.post('/update', async (req: Request, res: Response) => {
  const { error, value } = updateSchema.validate(req.body, { abortEarly: false });
  if (error) {
    return res.status(StatusCodes.BAD_REQUEST).json({ message: 'Validation failed', details: error.details });
  }
  const correlationId = value.correlationId || uuidv4();
  try {
    const result = await submitToFabric('update', { artifactId: value.artifactId, patch: value.patch, correlationId });
    return res.status(StatusCodes.OK).json({ success: true, correlationId, ...result });
  } catch (e: any) {
    return res.status(StatusCodes.INTERNAL_SERVER_ERROR).json({ success: false, correlationId, error: String(e?.message || e) });
  }
});

app.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`fabric-bridge listening on http://0.0.0.0:${PORT}`);
});


