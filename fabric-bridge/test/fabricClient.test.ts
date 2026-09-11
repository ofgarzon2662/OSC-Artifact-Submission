import {
  connectGateway,
  evaluateHistory,
  submitProvenanceTransaction
} from '../src/ts/fabricClient';

describe('Fabric Gateway client', () => {
  it('refuses a connection without a TLS server name', async () => {
    await expect(
      connectGateway({
        certificatePath: '',
        privateKeyPath: '',
        mspId: 'NSGMSP',
        channelName: 'osc-channel',
        chaincodeName: 'osc-provenance',
        peerEndpoint: 'peer0.nsg.example:7051',
        tlsCertPath: '',
        tlsServerName: ''
      })
    ).rejects.toThrow('TLS server name is required');
  });

  it('maps an artifact create to the namespaced provenance transaction', async () => {
    const getStatus = jest.fn().mockResolvedValue({
      successful: true,
      transactionId: 'tx-001',
      code: 0
    });
    const submit = jest.fn().mockResolvedValue({ getStatus });
    const endorse = jest.fn().mockResolvedValue({
      submit,
      getResult: () => Buffer.from('{"revision":1}')
    });
    const proposal = {
      getTransactionId: () => 'tx-001',
      endorse
    };
    const newProposal = jest.fn().mockReturnValue(proposal);
    const connection: any = { contract: { newProposal } };
    const request = {
      authenticatedUserId: 'user-001',
      organizationId: 'nsg',
      correlationId: 'corr-001',
      operation: 'artifact.create',
      requestedAt: '2026-09-01T22:00:00Z'
    };

    const result = await submitProvenanceTransaction(
      connection,
      'artifact',
      'create',
      '00000000-0000-4000-8000-000000000001',
      { title: 'Artifact' },
      request
    );

    expect(newProposal).toHaveBeenCalledWith('CreateArtifact', {
      arguments: [
        '00000000-0000-4000-8000-000000000001',
        '{"title":"Artifact"}',
        JSON.stringify(request)
      ]
    });
    expect(result).toMatchObject({ txId: 'tx-001', result: { revision: 1 } });
  });

  it('fails an unsuccessful Fabric commit', async () => {
    const connection: any = {
      contract: {
        newProposal: () => ({
          getTransactionId: () => 'tx-failed',
          endorse: async () => ({
            getResult: () => Buffer.from(''),
            submit: async () => ({
              getStatus: async () => ({
                successful: false,
                transactionId: 'tx-failed',
                code: 11
              })
            })
          })
        })
      }
    };
    await expect(
      submitProvenanceTransaction(
        connection,
        'workflow',
        'update',
        '00000000-0000-4000-8000-000000000002',
        { keywords: ['changed'] },
        {}
      )
    ).rejects.toThrow('status 11');
  });

  it('preserves peer diagnostics when an endorsement fails', async () => {
    const failure: any = new Error('endorsement failed');
    failure.details = [
      {
        address: 'peer0.nsg.example:7051',
        mspId: 'NSGMSP',
        message: 'artifact belongs to a different organization'
      }
    ];
    const connection: any = {
      contract: {
        newProposal: () => ({
          getTransactionId: () => 'tx-denied',
          endorse: jest.fn().mockRejectedValue(failure)
        })
      }
    };

    await expect(
      submitProvenanceTransaction(
        connection,
        'artifact',
        'create',
        '00000000-0000-4000-8000-000000000001',
        { title: 'Artifact' },
        {}
      )
    ).rejects.toMatchObject({
      message: expect.stringContaining('submission CreateArtifact failed'),
      details: [
        expect.objectContaining({
          mspId: 'NSGMSP',
          message: 'artifact belongs to a different organization'
        })
      ]
    });
  });

  it('evaluates workflow history and parses JSON', async () => {
    const evaluateTransaction = jest
      .fn()
      .mockResolvedValue(Buffer.from('[{"transactionId":"tx-001"}]'));
    const result = await evaluateHistory(
      { contract: { evaluateTransaction } } as any,
      'workflow',
      '00000000-0000-4000-8000-000000000002'
    );
    expect(evaluateTransaction).toHaveBeenCalledWith(
      'GetWorkflowHistory',
      '00000000-0000-4000-8000-000000000002'
    );
    expect(result).toEqual([{ transactionId: 'tx-001' }]);
  });

  it('adds operation context to history evaluation failures', async () => {
    const failure: any = new Error('peer unavailable');
    failure.cause = { details: 'connection refused' };
    const evaluateTransaction = jest.fn().mockRejectedValue(failure);

    await expect(
      evaluateHistory(
        { contract: { evaluateTransaction } } as any,
        'artifact',
        '00000000-0000-4000-8000-000000000001'
      )
    ).rejects.toMatchObject({
      message: expect.stringContaining('evaluation GetArtifactHistory failed'),
      details: [{ message: 'connection refused' }]
    });
  });
});
