'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');

const outputPath = process.env.ZCODE_OAUTH_CAPTURE_PATH;
const originalFetch = globalThis.fetch;

function fingerprint(value) {
  return crypto.createHash('sha256').update(String(value)).digest('hex').slice(0, 12);
}

function summarizeUrl(rawUrl) {
  const url = new URL(rawUrl);
  const summary = { origin: url.origin, pathname: url.pathname };
  if (url.pathname.includes('/oauth/cli/poll/')) {
    summary.flow_id_sha256_12 = fingerprint(url.pathname.split('/').pop() || '');
  }
  return summary;
}

function summarizeHeaders(headers) {
  const summary = {};
  for (const [name, value] of new Headers(headers).entries()) {
    const lower = name.toLowerCase();
    if (lower === 'authorization') {
      summary[lower] = {
        length: value.length,
        scheme: value.split(/\s+/, 1)[0] || '',
        sha256_12: fingerprint(value),
      };
    } else {
      summary[lower] = { length: value.length };
    }
  }
  return summary;
}

async function summarizeRequestBody(request) {
  const text = await request.clone().text();
  if (!text) return { length: 0 };
  try {
    const value = JSON.parse(text);
    return {
      keys: value && typeof value === 'object' ? Object.keys(value).sort() : [],
      provider: typeof value?.provider === 'string' ? value.provider : undefined,
    };
  } catch {
    return { length: text.length, type: 'non-json' };
  }
}

async function summarizeResponse(response) {
  const text = await response.clone().text();
  const summary = { http_status: response.status };
  if (!text) return summary;
  try {
    const envelope = JSON.parse(text);
    const data = envelope && typeof envelope.data === 'object' ? envelope.data : {};
    summary.business_code = typeof envelope?.code === 'number' ? envelope.code : undefined;
    summary.message = typeof envelope?.msg === 'string' ? envelope.msg : undefined;
    summary.data_status = typeof data.status === 'string' ? data.status : undefined;
    summary.poll_interval_sec = typeof data.poll_interval_sec === 'number' ? data.poll_interval_sec : undefined;
    summary.expires_at = typeof data.expires_at === 'number' ? data.expires_at : undefined;
    if (typeof data.flow_id === 'string') {
      summary.flow_id_sha256_12 = fingerprint(data.flow_id);
    }
    if (typeof data.poll_token === 'string') {
      summary.poll_token_sha256_12 = fingerprint(data.poll_token);
    }
    if (typeof data.authorize_url === 'string') {
      summary.authorize_url = summarizeUrl(data.authorize_url);
    }
  } catch {
    summary.body_length = text.length;
    summary.body_type = 'non-json';
  }
  return summary;
}

if (typeof originalFetch === 'function' && outputPath) {
  globalThis.fetch = async function capturedFetch(input, init) {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    const isOAuthCli = url.hostname === 'zcode.z.ai' && url.pathname.includes('/oauth/cli/');
    if (!isOAuthCli) return originalFetch(input, init);

    const requestRecord = {
      timestamp: new Date().toISOString(),
      direction: 'request',
      method: request.method,
      url: summarizeUrl(request.url),
      headers: summarizeHeaders(request.headers),
      body: await summarizeRequestBody(request),
    };
    fs.appendFileSync(outputPath, `${JSON.stringify(requestRecord)}\n`, { encoding: 'utf8', mode: 0o600 });

    const response = await originalFetch(input, init);
    const responseRecord = {
      timestamp: new Date().toISOString(),
      direction: 'response',
      request: summarizeUrl(request.url),
      response: await summarizeResponse(response),
    };
    fs.appendFileSync(outputPath, `${JSON.stringify(responseRecord)}\n`, { encoding: 'utf8', mode: 0o600 });
    return response;
  };
}
