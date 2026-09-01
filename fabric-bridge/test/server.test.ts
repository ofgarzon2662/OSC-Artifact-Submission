import request from 'supertest';

const TOKEN = 'test-ledger-gateway-token-00000001';
const ARTIFACT_ID = '00000000-0000-4000-8000-000000000001';
const WORKFLOW_ID = '00000000-0000-4000-8000-000000000002';
let app: any;

function organization() {
  return {
    id: 'nsg',
    name: 'nEUROSCIENCE GATEWAY',
    slug: 'nsg',
    mspId: 'NSGMSP'
  };
}

function metadata(operation: string, id = 'corr-001') {
  return {
    authenticatedUserId: 'user-nsg-001',
    organizationId: 'nsg',
    correlationId: id,
    operation,
    requestedAt: '2026-09-01T22:00:00.000Z'
  };
}

function artifactSubmit() {
  return {
    contractVersion: 'v3',
    artifactId: ARTIFACT_ID,
    organization: organization(),
    title: 'Reproducible microscopy analysis',
    visibility: 'private',
    footprint: 'a'.repeat(64),
    description: 'An experimental artifact used for deterministic tests.',
    manifest: [],
    keywords: ['provenance'],
    contributor: 'researcher@example.org',
    correlationId: 'corr-001',
    request: metadata('artifact.create')
  };
}

function workflowSubmit() {
  return {
    contractVersion: 'v3',
    workflowId: WORKFLOW_ID,
    organization: organization(),
    title: 'Microscopy processing workflow',
    visibility: 'private',
    description: 'A versioned research workflow.',
    githubRepositories: [],
    artifactIds: [ARTIFACT_ID],
    correlationId: 'corr-workflow-001',
    request: metadata('workflow.create', 'corr-workflow-001')
  };
}

function authenticated(test: request.Test): request.Test {
  return test.set('Authorization', `Bearer ${TOKEN}`);
}

beforeAll(async () => {
  process.env.NODE_ENV = 'test';
  process.env.FABRIC_REAL_MODE = 'false';
  process.env.MSP_ID = 'NSGMSP';
  process.env.ORGANIZATION_ID = 'nsg';
  process.env.LEDGER_GATEWAY_TOKEN = TOKEN;
  const module = await import('../src/server');
  app = module.app;
});

afterAll(() => {
  jest.restoreAllMocks();
});

describe('organization-bound ledger gateway', () => {
  it('reports a sanitized healthy configuration', async () => {
    const response = await request(app).get('/health').expect(200);
    expect(response.body).toEqual({
      status: 'healthy',
      service: 'ledger-gateway',
      organizationId: 'nsg',
      mspId: 'NSGMSP',
      fabricMode: 'simulation'
    });
    expect(JSON.stringify(response.body)).not.toContain(TOKEN);
    expect(JSON.stringify(response.body)).not.toContain('/wallet');
  });

  it('requires internal authentication', async () => {
    await request(app).post('/submit').send(artifactSubmit()).expect(401);
    await request(app)
      .post('/submit')
      .set('Authorization', 'Bearer wrong-token')
      .send(artifactSubmit())
      .expect(401);
  });

  it('accepts a valid NSG artifact create command', async () => {
    const response = await authenticated(request(app).post('/submit'))
      .send(artifactSubmit())
      .expect(200);
    expect(response.body).toMatchObject({
      success: true,
      correlationId: 'corr-001',
      peerMsp: 'NSGMSP',
      result: {
        assetId: ARTIFACT_ID,
        assetType: 'artifact',
        organizationId: 'nsg',
        organizationMsp: 'NSGMSP',
        revision: 1
      }
    });
  });

  it('rejects commands intended for another organization', async () => {
    const command = artifactSubmit();
    command.organization = {
      id: 'citizen-science',
      name: 'CITIZEN SCIENCE',
      slug: 'citizen-science',
      mspId: 'CitizenScienceMSP'
    };
    command.request.organizationId = 'citizen-science';
    const response = await authenticated(request(app).post('/submit'))
      .send(command)
      .expect(403);
    expect(response.body.message).toContain('does not match');
  });

  it('rejects mismatched correlation IDs', async () => {
    const command = artifactSubmit();
    command.request.correlationId = 'different-correlation';
    const response = await authenticated(request(app).post('/submit'))
      .send(command)
      .expect(403);
    expect(response.body.message).toContain('correlation IDs');
  });

  it('rejects an envelope whose request names a different organization', async () => {
    const command = artifactSubmit();
    command.request.organizationId = 'citizen-science';
    const response = await authenticated(request(app).post('/submit'))
      .send(command)
      .expect(403);
    expect(response.body.message).toContain('request organizations');
  });

  it('rejects a caller-supplied reserved identity field', async () => {
    const command = {
      contractVersion: 'v3',
      artifactId: ARTIFACT_ID,
      organization: organization(),
      patch: { keywords: ['safe'], mspId: 'CitizenScienceMSP' },
      correlationId: 'corr-update-001',
      request: metadata('artifact.update', 'corr-update-001')
    };
    const response = await authenticated(request(app).post('/update'))
      .send(command)
      .expect(400);
    expect(response.body.message).toBe('Validation failed');
  });

  it('rejects operation substitution and old contract versions', async () => {
    const wrongOperation = artifactSubmit();
    wrongOperation.request.operation = 'artifact.update';
    await authenticated(request(app).post('/submit'))
      .send(wrongOperation)
      .expect(400);

    const oldContract = artifactSubmit();
    oldContract.contractVersion = 'v2';
    await authenticated(request(app).post('/submit'))
      .send(oldContract)
      .expect(400);
  });

  it('accepts an artifact update with a bounded patch', async () => {
    const command = {
      contractVersion: 'v3',
      artifactId: ARTIFACT_ID,
      organization: organization(),
      patch: { keywords: ['reproducibility', 'provenance'] },
      correlationId: 'corr-update-001',
      request: metadata('artifact.update', 'corr-update-001')
    };
    const response = await authenticated(request(app).post('/update'))
      .send(command)
      .expect(200);
    expect(response.body.result.revision).toBe(2);
  });

  it('accepts workflow create and update commands', async () => {
    await authenticated(request(app).post('/workflow/submit'))
      .send(workflowSubmit())
      .expect(200);

    const update = {
      contractVersion: 'v3',
      workflowId: WORKFLOW_ID,
      organization: organization(),
      patch: { keywords: ['reproducible'] },
      correlationId: 'corr-workflow-update-001',
      request: metadata('workflow.update', 'corr-workflow-update-001')
    };
    const response = await authenticated(request(app).post('/workflow/update'))
      .send(update)
      .expect(200);
    expect(response.body.result).toMatchObject({
      assetType: 'workflow',
      revision: 2
    });
  });

  it('validates history IDs and authenticates history reads', async () => {
    await request(app).get(`/history/${ARTIFACT_ID}`).expect(401);
    await authenticated(request(app).get('/history/not-a-uuid')).expect(400);
    await authenticated(request(app).get(`/history/${ARTIFACT_ID}`))
      .expect(200, []);
    await authenticated(request(app).get(`/workflow/history/${WORKFLOW_ID}`))
      .expect(200, []);
  });

  it('rejects malformed create commands before any Fabric call', async () => {
    const command = artifactSubmit();
    delete (command as any).footprint;
    const response = await authenticated(request(app).post('/submit'))
      .send(command)
      .expect(400);
    expect(response.body.details.join(' ')).toContain('footprint');
  });
});

describe('Fabric failure handling', () => {
  it('returns a retryable 502 without exposing credentials', async () => {
    jest.resetModules();
    jest.doMock('../src/ts/fabricClient.ts', () => ({
      connectGateway: jest.fn().mockRejectedValue(new Error('peer unavailable')),
      evaluateHistory: jest.fn(),
      submitProvenanceTransaction: jest.fn()
    }));
    process.env.FABRIC_REAL_MODE = 'true';
    process.env.PEER_ENDPOINT = 'peer0.nsg.example:7051';
    process.env.TLS_CERT_PATH = '/mounted/tls/ca.pem';
    process.env.TLS_SERVER_NAME = 'peer0.nsg.example';
    process.env.CERTIFICATE_PATH = '/mounted/identity/cert.pem';
    process.env.PRIVATE_KEY_PATH = '/mounted/identity/key.pem';
    const module = await import('../src/server');
    const response = await authenticated(request(module.app).post('/submit'))
      .send(artifactSubmit())
      .expect(502);
    expect(response.body).toMatchObject({
      success: false,
      retryable: true,
      correlationId: 'corr-001',
      error: 'peer unavailable'
    });
    expect(JSON.stringify(response.body)).not.toContain(TOKEN);
  });

  it('submits create and update payloads and closes the Fabric connection', async () => {
    jest.resetModules();
    const close = jest.fn();
    const submitProvenanceTransaction = jest.fn().mockResolvedValue({
      txId: 'fabric-transaction-id',
      committedAt: '2026-09-01T22:01:00.000Z',
      result: { revision: 1 }
    });
    jest.doMock('../src/ts/fabricClient.ts', () => ({
      connectGateway: jest.fn().mockResolvedValue({ close }),
      evaluateHistory: jest.fn().mockResolvedValue([]),
      submitProvenanceTransaction
    }));
    const module = await import('../src/server');

    await authenticated(request(module.app).post('/submit'))
      .send(artifactSubmit())
      .expect(200);
    const update = {
      contractVersion: 'v3',
      artifactId: ARTIFACT_ID,
      organization: organization(),
      patch: { keywords: ['updated'] },
      correlationId: 'corr-real-update',
      request: metadata('artifact.update', 'corr-real-update')
    };
    await authenticated(request(module.app).post('/update')).send(update).expect(200);

    expect(submitProvenanceTransaction).toHaveBeenNthCalledWith(
      1,
      expect.anything(),
      'artifact',
      'create',
      ARTIFACT_ID,
      expect.objectContaining({ title: 'Reproducible microscopy analysis' }),
      expect.objectContaining({ operation: 'artifact.create' })
    );
    expect(submitProvenanceTransaction).toHaveBeenNthCalledWith(
      2,
      expect.anything(),
      'artifact',
      'update',
      ARTIFACT_ID,
      { keywords: ['updated'] },
      expect.objectContaining({ operation: 'artifact.update' })
    );
    expect(close).toHaveBeenCalledTimes(2);
  });

  it('returns Fabric history and closes the connection', async () => {
    jest.resetModules();
    const close = jest.fn();
    const history = [{ transactionId: 'tx-1', deleted: false }];
    const evaluateHistory = jest.fn().mockResolvedValue(history);
    jest.doMock('../src/ts/fabricClient.ts', () => ({
      connectGateway: jest.fn().mockResolvedValue({ close }),
      evaluateHistory,
      submitProvenanceTransaction: jest.fn()
    }));
    const module = await import('../src/server');
    const response = await authenticated(
      request(module.app).get(`/history/${ARTIFACT_ID}`)
    ).expect(200);
    expect(response.body).toEqual(history);
    expect(evaluateHistory).toHaveBeenCalledWith(
      expect.anything(),
      'artifact',
      ARTIFACT_ID
    );
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('returns a retryable error when a history evaluation fails', async () => {
    jest.resetModules();
    jest.doMock('../src/ts/fabricClient.ts', () => ({
      connectGateway: jest.fn().mockRejectedValue(new Error('history peer unavailable')),
      evaluateHistory: jest.fn(),
      submitProvenanceTransaction: jest.fn()
    }));
    const module = await import('../src/server');
    const response = await authenticated(
      request(module.app).get(`/history/${ARTIFACT_ID}`)
    ).expect(502);
    expect(response.body).toMatchObject({
      success: false,
      retryable: true,
      error: 'history peer unavailable'
    });
  });
});
