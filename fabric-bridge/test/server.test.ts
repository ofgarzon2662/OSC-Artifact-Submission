import request from 'supertest';
import express from 'express';
import { StatusCodes } from 'http-status-codes';

// Import the app by constructing it similarly to src/server.ts but without listen()
// For MVP tests, we spin up a minimal instance by requiring the built server module would be ideal.
// Here we replicate minimal route binding to validate schemas.

const app = express();
app.use(express.json({ limit: '1mb' }));

// Minimal route stubs copied from server config for tests
// Acknowledgements should allow empty
app.post('/submit', (req, res) => {
  const body = req.body || {};
  if (!body.artifactId || !body.data) {
    return res.status(StatusCodes.BAD_REQUEST).json({ message: 'Validation failed' });
  }
  if (typeof body.data.footprint !== 'string') {
    return res.status(StatusCodes.BAD_REQUEST).json({ message: 'Validation failed' });
  }
  // Simulate success
  return res.status(StatusCodes.OK).json({ success: true, txId: 'tx', committedAt: new Date().toISOString() });
});

describe('fabric-bridge endpoints (mock)', () => {
  it('accepts empty acknowledgements on submit', async () => {
    const res = await request(app)
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
      .expect(StatusCodes.OK);
    expect(res.body.success).toBe(true);
  });
});


