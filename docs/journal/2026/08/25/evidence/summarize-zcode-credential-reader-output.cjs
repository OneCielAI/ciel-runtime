'use strict';

const crypto = require('node:crypto');

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  try {
    const payload = JSON.parse(input);
    const value = String(payload.value || '');
    const encodedClaims = value.split('.')[1] || '';
    const claims = encodedClaims
      ? JSON.parse(Buffer.from(encodedClaims, 'base64url').toString('utf8'))
      : {};
    process.stdout.write(JSON.stringify({
      present: value.length > 0,
      length: value.length,
      sha256_12: crypto.createHash('sha256').update(value).digest('hex').slice(0, 12),
      iat: typeof claims.iat === 'number' ? claims.iat : null,
      subject_sha256_12: claims.sub
        ? crypto.createHash('sha256').update(String(claims.sub)).digest('hex').slice(0, 12)
        : null,
      user_id_sha256_12: claims.user_id
        ? crypto.createHash('sha256').update(String(claims.user_id)).digest('hex').slice(0, 12)
        : null,
      claim_keys: Object.keys(claims).sort(),
    }));
  } catch {
    process.stderr.write('Credential summary failed.\n');
    process.exitCode = 1;
  }
});
