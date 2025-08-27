import express, { Request, Response } from 'express';
import morgan from 'morgan';
import dotenv from 'dotenv';
import { StatusCodes } from 'http-status-codes';
import Joi from 'joi';
import { v4 as uuidv4 } from 'uuid';

dotenv.config();

// Configuration
const PORT = parseInt(process.env.PORT || '4000', 10);
const FABRIC_CHANNEL = process.env.FABRIC_CHANNEL || 'mychannel';
const FABRIC_CHAINCODE = process.env.FABRIC_CHAINCODE || 'artifacts';
const FABRIC_PEER = process.env.FABRIC_PEER || 'localhost:7051';
const WALLET_PATH = process.env.WALLET_PATH || './wallet/org1/svc-org1';
const MSP_ID = process.env.MSP_ID || 'Org1MSP';
const IDENTITY_LABEL = process.env.IDENTITY_LABEL || 'svc-org1';

// Minimal request schemas
const submitSchema = Joi.object({
  artifactId: Joi.string().uuid().required(),
  data: Joi.object().required(),
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


