import request from 'supertest';

let app: any;

beforeAll(async () => {
  process.env.NODE_ENV = 'test';
  process.env.FABRIC_REAL_MODE = 'false';
  const mod = await import('../src/server');
  app = (mod as any).app;
});

describe('fabric-bridge endpoints', () => {
  it('GET /health returns 200', async () => {
    await request(app).get('/health').expect(200);
  });

  it('POST /submit accepts empty acknowledgements', async () => {
    await request(app)
      .post('/submit')
      .send({
        artifactId: '00000000-0000-4000-8000-000000000001',
        data: {
          title: 't',
          description: 'x'.repeat(60),
          manifest: [],
          keywords: [],
          links: [],
          dois: [],
          fundingAgencies: [],
          acknowledgements: '',
          footprint: '0'.repeat(64)
        }
      })
      .expect(200);
  });

  it('POST /submit fails validation on missing footprint', async () => {
    const res = await request(app)
      .post('/submit')
      .send({
        artifactId: '00000000-0000-4000-8000-000000000002',
        data: {
          title: 't',
          description: 'x'.repeat(60),
          manifest: []
        }
      })
      .expect(400);
    expect(res.body.message).toBe('Validation failed');
  });

  it('POST /update succeeds with minimal patch', async () => {
    await request(app)
      .post('/update')
      .send({
        artifactId: '00000000-0000-4000-8000-000000000003',
        patch: { keywords: ['ai'] }
      })
      .expect(200);
  });

  it('GET /history with bad UUID returns 400', async () => {
    await request(app).get('/history/not-a-uuid').expect(400);
  });

  it('POST /submit injects submitter defaults when missing', async () => {
    delete process.env.SUBMITTER_EMAIL_DEFAULT;
    delete process.env.SUBMITTER_USERNAME_DEFAULT;
    process.env.SUBMITTER_EMAIL_DEFAULT = 'svc@org1.example.com';
    process.env.SUBMITTER_USERNAME_DEFAULT = 'svc-org1';
    const res = await request(app)
      .post('/submit')
      .send({
        artifactId: '00000000-0000-4000-8000-000000000004',
        data: {
          title: 't',
          description: 'x'.repeat(60),
          manifest: [],
          footprint: 'a'.repeat(64)
        }
      })
      .expect(200);
    expect(res.body.success).toBe(true);
  });

  it('GET /history/:id returns 200 with mocked gateway', async () => {
    // Re-import server with mocked fabric client
    jest.resetModules();
    jest.doMock('../src/ts/fabricClient.ts', () => ({
      connectGateway: jest.fn().mockResolvedValue({ close: jest.fn() }),
      evaluateHistory: jest.fn().mockResolvedValue('[]'),
      submitTxIfReal: jest.fn()
    }));
    const mod = await import('../src/server');
    const appMocked = (mod as any).app;
    await request(appMocked)
      .get('/history/00000000-0000-4000-8000-000000000005')
      .expect(200);
  });

  it('POST /submit returns 500 when Fabric submission fails', async () => {
    jest.resetModules();
    jest.doMock('../src/ts/fabricClient.ts', () => ({
      connectGateway: jest.fn(),
      evaluateHistory: jest.fn(),
      submitTxIfReal: jest.fn().mockRejectedValue(new Error('boom'))
    }));
    process.env.FABRIC_REAL_MODE = 'true';
    const mod = await import('../src/server');
    const appMocked = (mod as any).app;
    await request(appMocked)
      .post('/submit')
      .send({
        artifactId: '00000000-0000-4000-8000-000000000006',
        data: {
          title: 't',
          description: 'x'.repeat(60),
          manifest: [],
          footprint: 'b'.repeat(64)
        }
      })
      .expect(500);
  });
});


