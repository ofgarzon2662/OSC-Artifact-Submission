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
});


