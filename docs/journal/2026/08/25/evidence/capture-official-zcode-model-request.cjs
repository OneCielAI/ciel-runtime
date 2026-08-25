'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');

const outputPath = process.env.ZCODE_REQUEST_CAPTURE_PATH;
const originalFetch = globalThis.fetch;

function fingerprint(value) {
  return crypto.createHash('sha256').update(value).digest('hex').slice(0, 12);
}

function headerSummary(headers) {
  const result = {};
  for (const [name, value] of new Headers(headers).entries()) {
    const lower = name.toLowerCase();
    if (lower === 'authorization') {
      result[lower] = {
        length: value.length,
        scheme: value.split(/\s+/, 1)[0] || '',
        sha256_12: fingerprint(value),
      };
    } else if (lower === 'x-api-key') {
      result[lower] = {
        length: value.length,
        sha256_12: fingerprint(value),
      };
    } else if (lower === 'x-aliyun-captcha-verify-param') {
      result[lower] = {
        length: value.length,
        sha256_12: fingerprint(value),
      };
    } else if (lower === 'x-session-id') {
      result[lower] = {
        length: value.length,
        prefix: value.slice(0, 5),
      };
    } else {
      result[lower] = { length: value.length };
    }
  }
  return result;
}

function requestBodySummary(body) {
  if (typeof body !== 'string') return { type: typeof body };
  try {
    const parsed = JSON.parse(body);
    return {
      keys: Object.keys(parsed).sort(),
      model: parsed.model,
      stream: parsed.stream,
      max_tokens: parsed.max_tokens,
    };
  } catch {
    return { length: body.length, type: 'string' };
  }
}

if (typeof originalFetch === 'function' && outputPath) {
  globalThis.fetch = async function capturedFetch(input, init) {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    if (url.hostname === 'zcode.z.ai' && url.pathname.includes('/zcode-plan/')) {
      const body = await request.clone().text();
      const record = {
        timestamp: new Date().toISOString(),
        method: request.method,
        url: request.url,
        headers: headerSummary(request.headers),
        body: requestBodySummary(body),
      };
      fs.appendFileSync(outputPath, `${JSON.stringify(record)}\n`, {
        encoding: 'utf8',
        mode: 0o600,
      });
    }
    return originalFetch(input, init);
  };
}
