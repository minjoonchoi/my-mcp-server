// Passive CDP streaming keeps actual SSE bytes observable after the application
// cancels its reader. It never intercepts, replaces, clones or repeats a request.
import { createHash } from 'node:crypto';

const RESPONSE_LIMIT = 128 * 1024;
const TOTAL_LIMIT = 4 * 1024 * 1024;
const keyOf = body => createHash('sha256').update(body).digest('hex');

export async function captureMcpResponses(page) {
  const session = await page.context().newCDPSession(page);
  const byId = new Map(), byKey = new Map(), waiting = new Map();
  let retained = 0;
  const release = record => {
    retained -= record.bytes;
    record.bytes = 0; record.prefix = null; record.chunks = [];
    byId.delete(record.requestId); byKey.delete(record.key);
  };
  const append = (record, base64, prefix = false) => {
    const bytes = Buffer.from(base64, 'base64');
    if (record.bytes + bytes.length > RESPONSE_LIMIT || retained + bytes.length > TOTAL_LIMIT) {
      record.error = 'Passive MCP response capture exceeded its byte limit';
      return;
    }
    record.bytes += bytes.length; retained += bytes.length;
    if (prefix) record.prefix = bytes;
    else record.chunks.push(bytes);
  };
  const startStreaming = async record => {
    try {
      const { bufferedData } = await session.send('Network.streamResourceContent', { requestId: record.requestId });
      append(record, bufferedData, true);
    } catch {
      record.error = 'Passive MCP response streaming was unavailable';
    } finally { record.streamingReady(); }
  };
  session.on('Network.requestWillBeSent', ({ requestId, request }) => {
    if (new URL(request.url).pathname !== '/mcp' || request.method !== 'POST' || !request.postData) return;
    if (request.postData.length > 4096 || byId.size >= 128) return;
    let rpc;
    try { rpc = JSON.parse(request.postData); } catch { return; }
    if (!Number.isSafeInteger(rpc.id)) return; // Initialized notifications have no response body.
    const key = keyOf(request.postData);
    const record = { requestId, key, rpcId: rpc.id, chunks: [], prefix: null, bytes: 0, error: null };
    record.finished = new Promise(resolve => { record.finish = resolve; });
    record.streamed = new Promise(resolve => { record.streamingReady = resolve; });
    byId.set(requestId, record); byKey.set(key, record);
    waiting.get(key)?.(record); waiting.delete(key);
    // Start before response headers: the application can read and cancel a
    // complete SSE frame before a responseReceived observer gets its command in.
    void startStreaming(record);
  });
  session.on('Network.dataReceived', ({ requestId, data }) => {
    const record = byId.get(requestId);
    if (record && data) append(record, data);
  });
  for (const event of ['Network.loadingFinished', 'Network.loadingFailed']) {
    session.on(event, ({ requestId }) => byId.get(requestId)?.finish());
  }
  await session.send('Network.enable', { maxTotalBufferSize: TOTAL_LIMIT, maxResourceBufferSize: RESPONSE_LIMIT, maxPostDataSize: 4096 });
  return {
    async text(response) {
      const requestBody = response.request().postData();
      const key = keyOf(requestBody);
      let timer, record;
      const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Passive MCP response capture timed out')), 15000); });
      try {
        record = byKey.get(key) || await Promise.race([new Promise(resolve => waiting.set(key, resolve)), timeout]);
        await Promise.race([Promise.all([record.streamed, record.finished]), timeout]);
        if (record.error) throw new Error(record.error);
        const raw = Buffer.concat([record.prefix || Buffer.alloc(0), ...record.chunks]).toString('utf8');
        const data = raw.startsWith('event:') || raw.startsWith('data:')
          ? raw.split('\n').find(line => line.startsWith('data:'))?.slice(5).trim() : raw;
        const value = JSON.parse(data);
        if (value.id !== record.rpcId) throw new Error('Captured MCP response does not match the original request ID');
        return raw;
      } finally {
        clearTimeout(timer); waiting.delete(key);
        if (record) release(record);
      }
    },
    async dispose() {
      await session.detach();
      for (const record of byId.values()) release(record);
      waiting.clear();
    },
  };
}
