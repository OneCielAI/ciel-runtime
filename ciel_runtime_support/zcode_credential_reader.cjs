'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');

const encryptedPrefix = 'enc:v1:';

function credentialSecret() {
  const explicit = String(process.env.ZCODE_CREDENTIAL_SECRET || '').trim();
  if (explicit) return explicit;
  let username = 'unknown';
  try {
    username = os.userInfo().username;
  } catch {
    // This is the exact fallback used by the installed official ZCode runtime.
  }
  return `zcode-credential-fallback:${process.platform}:${os.homedir()}:${username}`;
}

function decrypt(value) {
  if (!value.startsWith(encryptedPrefix)) return value;
  const parts = value.slice(encryptedPrefix.length).split('.');
  if (parts.length !== 3 || parts.some((part) => !part)) {
    throw new Error('invalid ciphertext format');
  }
  const [ivText, tagText, ciphertextText] = parts;
  const iv = Buffer.from(ivText, 'base64url');
  const tag = Buffer.from(tagText, 'base64url');
  const ciphertext = Buffer.from(ciphertextText, 'base64url');
  if (iv.length !== 12 || tag.length !== 16) {
    throw new Error('invalid ciphertext dimensions');
  }
  const key = crypto.createHash('sha256').update(credentialSecret()).digest();
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString('utf8');
}

function main() {
  const [credentialPath, credentialKey] = process.argv.slice(2);
  if (!credentialPath || !credentialKey) throw new Error('missing arguments');
  const record = JSON.parse(fs.readFileSync(credentialPath, 'utf8'));
  const stored = record && typeof record[credentialKey] === 'string'
    ? record[credentialKey]
    : '';
  const value = stored ? decrypt(stored) : '';
  process.stdout.write(JSON.stringify({ value }));
}

try {
  main();
} catch {
  process.stderr.write('ZCode shared credential read failed.\n');
  process.exitCode = 1;
}
