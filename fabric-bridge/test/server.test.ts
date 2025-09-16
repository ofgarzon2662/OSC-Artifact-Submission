import request from 'supertest';
import { app } from '../src/server';

describe('fabric-bridge endpoints (validation)', () => {
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
      .expect(200);
  });
});


