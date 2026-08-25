#!/usr/bin/env node
'use strict';

// Diagnostic only: load the official ZCode credential store from an in-memory
// copy of the installed runtime. Never print credential material.
const crypto = require('node:crypto');
const fs = require('node:fs');
const Module = require('node:module');
const os = require('node:os');
const path = require('node:path');

const vendorPath = process.argv[2] || path.join(
  os.homedir(),
  '.npm-global/lib/node_modules/zcode-app-cli/vendor/zcode.cjs',
);

function loadCredentialReader() {
  const source = fs.readFileSync(vendorPath, 'utf8');
  const legacyMarker = 'RMi();';
  const currentMarker = 'D8i();';
  const marker = source.lastIndexOf(currentMarker) >= 0 ? currentMarker : legacyMarker;
  const markerAt = source.lastIndexOf(marker);
  if (markerAt < 0) throw new Error('Official ZCode entrypoint marker was not found.');

  const replacement = marker === currentMarker
    ? [
      'Pot();',
      'module.exports = {',
      '  loadJwt: async () => await ree({ env: process.env }).load("zcodejwttoken"),',
      '};',
    ].join('\n')
    : [
      'Mrt();',
      'module.exports = {',
      '  loadJwt: async () => await _X().load(Ib.zcodeJwtToken),',
      '};',
    ].join('\n');
  const diagnosticSource = `${source.slice(0, markerAt)}${replacement}${source.slice(markerAt + marker.length)}`;
  const diagnosticModule = new Module(`${vendorPath}.credential-probe`, module);
  diagnosticModule.filename = vendorPath;
  diagnosticModule.paths = Module._nodeModulePaths(path.dirname(vendorPath));
  diagnosticModule._compile(diagnosticSource, vendorPath);
  return diagnosticModule.exports;
}

function safeBody(text) {
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    return text.slice(0, 500);
  }
  const sensitive = /token|secret|authorization|credential|key/i;
  const redact = (input) => {
    if (Array.isArray(input)) return input.map(redact);
    if (!input || typeof input !== 'object') return input;
    return Object.fromEntries(Object.entries(input).map(([key, item]) => [
      key,
      sensitive.test(key) ? '<redacted>' : redact(item),
    ]));
  };
  return redact(value);
}

async function main() {
  const { loadJwt } = loadCredentialReader();
  const jwt = await loadJwt();
  if (typeof jwt !== 'string' || !jwt.trim()) throw new Error('Official ZCode JWT is absent.');
  const fingerprint = crypto.createHash('sha256').update(jwt).digest('hex').slice(0, 12);
  console.log(JSON.stringify({ credential: { present: true, sha256_12: fingerprint } }));

  const outputPath = process.env.ZCODE_JWT_OUTPUT_PATH;
  if (outputPath) {
    fs.writeFileSync(outputPath, jwt, { encoding: 'utf8', mode: 0o600 });
    return;
  }

  const base = 'https://zcode.z.ai/api/v1/zcode-plan';
  const telemetryPath = path.join(os.homedir(), '.zcode/v2/telemetry-state.json');
  const telemetry = JSON.parse(fs.readFileSync(telemetryPath, 'utf8'));
  const headers = {
    Accept: 'application/json',
    Authorization: jwt,
    'HTTP-Referer': 'https://zcode.z.ai',
    'User-Agent': 'ZCode/3.9.1',
    'X-Client-Language': Intl.DateTimeFormat().resolvedOptions().locale || 'unknown',
    'X-Client-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone || 'unknown',
    'X-Device-Mid': telemetry.deviceMid,
    'X-Os-Category': 'linux',
    'X-Os-Version': os.release(),
    'X-Platform': `${process.platform}-${process.arch}`,
    'X-Release-Channel': 'stable',
    'X-Title': 'Z Code@electron',
    'X-ZCode-App-Version': '3.9.1',
  };
  for (const endpoint of ['billing/current', 'billing/balance']) {
    const url = `${base}/${endpoint}?app_version=3.9.1`;
    const response = await fetch(url, { headers });
    const body = safeBody(await response.text());
    console.log(JSON.stringify({ endpoint, status: response.status, body }));
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }));
  process.exitCode = 1;
});
