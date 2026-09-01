import { timingSafeEqual } from 'node:crypto';
import express, { NextFunction, Request, Response } from 'express';
import morgan from 'morgan';
import dotenv from 'dotenv';
import { StatusCodes } from 'http-status-codes';
import Joi from 'joi';
import { v4 as uuidv4 } from 'uuid';
import {
  connectGateway,
  evaluateHistory,
  ProvenanceAction,
  ProvenanceAssetType,
  submitProvenanceTransaction
} from './ts/fabricClient.js';

dotenv.config();

const PORT = Number.parseInt(process.env.PORT || '4000', 10);
const FABRIC_REAL_MODE = /^true$/i.test(process.env.FABRIC_REAL_MODE || 'true');
const FABRIC_CHANNEL = process.env.FABRIC_CHANNEL || 'osc-channel';
const FABRIC_CHAINCODE = process.env.FABRIC_CHAINCODE || 'osc-provenance';
const PEER_ENDPOINT = process.env.PEER_ENDPOINT || '';
const TLS_CERT_PATH = process.env.TLS_CERT_PATH || '';
const TLS_SERVER_NAME = process.env.TLS_SERVER_NAME || '';
const CERTIFICATE_PATH = process.env.CERTIFICATE_PATH || '';
const PRIVATE_KEY_PATH = process.env.PRIVATE_KEY_PATH || '';
const MSP_ID = process.env.MSP_ID || '';
const ORGANIZATION_ID = process.env.ORGANIZATION_ID || '';
const LEDGER_GATEWAY_TOKEN = process.env.LEDGER_GATEWAY_TOKEN || '';

const supportedOrganizations: Record<string, string> = {
  NSGMSP: 'nsg',
  CitizenScienceMSP: 'citizen-science'
};

function validateConfiguration(): string[] {
  const errors: string[] = [];
  if (!(MSP_ID in supportedOrganizations)) errors.push('MSP_ID is not supported');
  if (supportedOrganizations[MSP_ID] !== ORGANIZATION_ID) {
    errors.push('ORGANIZATION_ID does not match MSP_ID');
  }
  if (LEDGER_GATEWAY_TOKEN.length < 24) {
    errors.push('LEDGER_GATEWAY_TOKEN must contain at least 24 characters');
  }
  if (FABRIC_REAL_MODE) {
    if (!PEER_ENDPOINT) errors.push('PEER_ENDPOINT is required');
    if (!TLS_CERT_PATH) errors.push('TLS_CERT_PATH is required');
    if (!TLS_SERVER_NAME) errors.push('TLS_SERVER_NAME is required');
    if (!CERTIFICATE_PATH) errors.push('CERTIFICATE_PATH is required');
    if (!PRIVATE_KEY_PATH) errors.push('PRIVATE_KEY_PATH is required');
  }
  return errors;
}

const configurationErrors = validateConfiguration();
const correlationId = Joi.string()
  .pattern(/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/)
  .required();

const requestSchema = (operation: string) =>
  Joi.object({
    authenticatedUserId: Joi.string().trim().min(1).max(128).required(),
    organizationId: Joi.string().valid('nsg', 'citizen-science').required(),
    correlationId,
    operation: Joi.string().valid(operation).required(),
    requestedAt: Joi.string().isoDate().required()
  })
    .unknown(false)
    .required();

const organizationSchema = Joi.object({
  id: Joi.string().valid('nsg', 'citizen-science').required(),
  name: Joi.string().trim().min(1).max(200).required(),
  slug: Joi.string().trim().max(128).optional(),
  mspId: Joi.string().valid('NSGMSP', 'CitizenScienceMSP').required(),
  ledgerGroupName: Joi.string().trim().max(128).optional(),
  ledgerApiUserId: Joi.string().trim().max(128).optional(),
  artifactSchemaName: Joi.string().trim().max(128).optional()
})
  .unknown(false)
  .required();

const uuid = Joi.string().guid({ version: ['uuidv4'] }).required();
const textArray = Joi.array().items(Joi.string().max(2048)).max(500);
const artifactCreateData = {
  title: Joi.string().trim().min(1).max(500).required(),
  visibility: Joi.string().valid('public', 'private').optional(),
  footprint: Joi.string().hex().length(64).required(),
  description: Joi.string().allow('').max(50_000).optional(),
  submission_comment: Joi.string().allow('').max(5_000).optional(),
  keywords: textArray.optional(),
  links: Joi.array().items(Joi.any()).max(500).optional(),
  dois: textArray.optional(),
  fundingAgencies: Joi.array().items(Joi.any()).max(500).optional(),
  acknowledgements: Joi.string().allow('').max(20_000).optional(),
  manifest: Joi.array().items(Joi.any()).max(10_000).required(),
  contributor: Joi.string().allow('').max(320).optional()
};
const workflowCreateData = {
  title: Joi.string().trim().min(1).max(500).required(),
  visibility: Joi.string().valid('public', 'private').optional(),
  description: Joi.string().allow('').max(50_000).optional(),
  submission_comment: Joi.string().allow('').max(5_000).optional(),
  keywords: textArray.optional(),
  githubRepositories: Joi.array().items(Joi.any()).max(500).optional(),
  artifactIds: Joi.array().items(uuid).max(10_000).optional(),
  contributor: Joi.string().allow('').max(320).optional()
};

const commonEnvelope = {
  contractVersion: Joi.string().valid('v3').required(),
  organization: organizationSchema,
  correlationId
};

const artifactSubmitSchema = Joi.object({
  ...commonEnvelope,
  artifactId: uuid,
  ...artifactCreateData,
  request: requestSchema('artifact.create')
}).unknown(false);

const artifactUpdateSchema = Joi.object({
  ...commonEnvelope,
  artifactId: uuid,
  patch: Joi.object(
    Object.fromEntries(
      Object.entries(artifactCreateData).map(([key, schema]) => [
        key,
        (schema as Joi.AnySchema).optional()
      ])
    )
  )
    .min(1)
    .unknown(false)
    .required(),
  contributor: Joi.string().allow('').max(320).optional(),
  request: requestSchema('artifact.update')
}).unknown(false);

const workflowSubmitSchema = Joi.object({
  ...commonEnvelope,
  workflowId: uuid,
  ...workflowCreateData,
  request: requestSchema('workflow.create')
}).unknown(false);

const workflowUpdateSchema = Joi.object({
  ...commonEnvelope,
  workflowId: uuid,
  patch: Joi.object(
    Object.fromEntries(
      Object.entries(workflowCreateData).map(([key, schema]) => [
        key,
        (schema as Joi.AnySchema).optional()
      ])
    )
  )
    .min(1)
    .unknown(false)
    .required(),
  contributor: Joi.string().allow('').max(320).optional(),
  request: requestSchema('workflow.update')
}).unknown(false);

function bearerToken(req: Request): string {
  const match = /^Bearer\s+(.+)$/i.exec(req.header('authorization') || '');
  return match?.[1] || '';
}

function authorized(req: Request): boolean {
  const supplied = Buffer.from(bearerToken(req));
  const expected = Buffer.from(LEDGER_GATEWAY_TOKEN);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

function requireInternalAuthentication(
  req: Request,
  res: Response,
  next: NextFunction
): void {
  if (!authorized(req)) {
    res.status(StatusCodes.UNAUTHORIZED).json({ message: 'Unauthorized' });
    return;
  }
  next();
}

function validateEnvelope(value: any): string | null {
  if (value.organization.id !== ORGANIZATION_ID || value.organization.mspId !== MSP_ID) {
    return 'Command organization does not match this ledger gateway identity';
  }
  if (value.correlationId !== value.request.correlationId) {
    return 'Envelope and request correlation IDs must match';
  }
  if (value.organization.id !== value.request.organizationId) {
    return 'Envelope and request organizations must match';
  }
  return null;
}

function payloadFor(value: any, action: ProvenanceAction): Record<string, unknown> {
  if (action === 'update') return value.patch;
  const excluded = new Set([
    'contractVersion',
    'artifactId',
    'workflowId',
    'organization',
    'correlationId',
    'request'
  ]);
  return Object.fromEntries(
    Object.entries(value).filter(([key]) => !excluded.has(key))
  );
}

function fabricConfiguration() {
  return {
    certificatePath: CERTIFICATE_PATH,
    privateKeyPath: PRIVATE_KEY_PATH,
    mspId: MSP_ID,
    channelName: FABRIC_CHANNEL,
    chaincodeName: FABRIC_CHAINCODE,
    peerEndpoint: PEER_ENDPOINT,
    tlsCertPath: TLS_CERT_PATH,
    tlsServerName: TLS_SERVER_NAME
  };
}

async function submit(
  assetType: ProvenanceAssetType,
  action: ProvenanceAction,
  value: any
) {
  const assetId = assetType === 'artifact' ? value.artifactId : value.workflowId;
  if (!FABRIC_REAL_MODE) {
    return {
      txId: uuidv4().replace(/-/g, ''),
      committedAt: new Date().toISOString(),
      result: {
        assetId,
        assetType,
        organizationId: ORGANIZATION_ID,
        organizationMsp: MSP_ID,
        revision: action === 'create' ? 1 : 2
      }
    };
  }
  const connection = await connectGateway(fabricConfiguration());
  try {
    return await submitProvenanceTransaction(
      connection,
      assetType,
      action,
      assetId,
      payloadFor(value, action),
      value.request
    );
  } finally {
    connection.close();
  }
}

function commandHandler(
  assetType: ProvenanceAssetType,
  action: ProvenanceAction,
  schema: Joi.ObjectSchema
) {
  return async (req: Request, res: Response) => {
    const validation = schema.validate(req.body, {
      abortEarly: false,
      convert: false
    });
    if (validation.error) {
      return res.status(StatusCodes.BAD_REQUEST).json({
        message: 'Validation failed',
        details: validation.error.details.map((detail) => detail.message)
      });
    }
    const envelopeError = validateEnvelope(validation.value);
    if (envelopeError) {
      return res.status(StatusCodes.FORBIDDEN).json({ message: envelopeError });
    }
    try {
      const result = await submit(assetType, action, validation.value);
      return res.status(StatusCodes.OK).json({
        success: true,
        correlationId: validation.value.correlationId,
        peerMsp: MSP_ID,
        ...result
      });
    } catch (error: any) {
      return res.status(StatusCodes.BAD_GATEWAY).json({
        success: false,
        retryable: true,
        correlationId: validation.value.correlationId,
        error: error?.message || String(error)
      });
    }
  };
}

function historyHandler(assetType: ProvenanceAssetType) {
  return async (req: Request, res: Response) => {
    const validation = uuid.validate(req.params.assetId, { convert: false });
    if (validation.error) {
      return res.status(StatusCodes.BAD_REQUEST).json({ message: 'Invalid asset ID' });
    }
    if (!FABRIC_REAL_MODE) return res.status(StatusCodes.OK).json([]);
    try {
      const connection = await connectGateway(fabricConfiguration());
      try {
        const history = await evaluateHistory(connection, assetType, validation.value);
        return res.status(StatusCodes.OK).json(history);
      } finally {
        connection.close();
      }
    } catch (error: any) {
      return res.status(StatusCodes.BAD_GATEWAY).json({
        success: false,
        retryable: true,
        error: error?.message || String(error)
      });
    }
  };
}

export const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '1mb', strict: true }));
app.use(morgan('combined'));

app.get('/health', (_req: Request, res: Response) => {
  res.status(configurationErrors.length ? StatusCodes.SERVICE_UNAVAILABLE : StatusCodes.OK).json({
    status: configurationErrors.length ? 'misconfigured' : 'healthy',
    service: 'ledger-gateway',
    organizationId: ORGANIZATION_ID || 'unconfigured',
    mspId: MSP_ID || 'unconfigured',
    fabricMode: FABRIC_REAL_MODE ? 'gateway' : 'simulation'
  });
});

app.use(requireInternalAuthentication);
app.post('/submit', commandHandler('artifact', 'create', artifactSubmitSchema));
app.post('/update', commandHandler('artifact', 'update', artifactUpdateSchema));
app.post('/workflow/submit', commandHandler('workflow', 'create', workflowSubmitSchema));
app.post('/workflow/update', commandHandler('workflow', 'update', workflowUpdateSchema));
app.get('/history/:assetId', historyHandler('artifact'));
app.get('/workflow/history/:assetId', historyHandler('workflow'));

if (process.env.NODE_ENV !== 'test') {
  if (configurationErrors.length) {
    throw new Error(`Invalid ledger gateway configuration: ${configurationErrors.join('; ')}`);
  }
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`ledger-gateway listening on port ${PORT}`);
  });
}
