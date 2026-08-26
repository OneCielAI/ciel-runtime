#!/usr/bin/env node
'use strict';

const path = require('node:path');
const { _electron: electron } = require(process.env.PLAYWRIGHT_CORE_PATH);

async function main() {
  const appRoot = process.env.ZCODE_APP_ROOT;
  if (!appRoot) throw new Error('ZCODE_APP_ROOT is required.');
  const app = await electron.launch({
    executablePath: path.join(appRoot, 'zcode'),
    args: ['--no-sandbox', '--disable-gpu'],
    env: { ...process.env, APPDIR: appRoot },
    timeout: 30_000,
  });
  let window = await app.firstWindow({ timeout: 30_000 });
  await window.waitForTimeout(10_000);
  let bestLength = -1;
  for (const candidate of app.windows()) {
    const length = (await candidate.locator('body').innerText().catch(() => '')).length;
    if (length > bestLength) {
      bestLength = length;
      window = candidate;
    }
  }
  if (process.env.ZCODE_SEND_PROBE === '1') {
    const back = window.getByText('Back to workspace', { exact: true });
    if (await back.count()) await back.first().click();
    await window.waitForTimeout(2_000);
    const newTask = window.getByRole('button', { name: 'New task', exact: true });
    if (await newTask.count()) await newTask.first().click();
    await window.waitForTimeout(2_000);
    const chooser = window.getByRole('button', { name: /choose workspace/i });
    if (await chooser.count()) {
      await chooser.first().click();
      await window.waitForTimeout(1_000);
      const outside = window.getByText('Work outside a project', { exact: true });
      if (await outside.count()) await outside.first().click();
      await window.waitForTimeout(3_000);
    }
    const editor = window.locator('[contenteditable="true"], textarea').first();
    await editor.fill('Reply with exactly OK.');
    await window.getByRole('button', { name: 'Send', exact: true }).click();
    await window.waitForFunction(() => {
      const text = document.body?.innerText || '';
      return text.includes('Please complete the captcha') || /(^|\n)OK($|\n)/m.test(text);
    }, undefined, { timeout: 120_000 });
    const text = await window.locator('body').innerText();
    console.log(JSON.stringify({ probeState: text.includes('Please complete the captcha') ? 'captcha' : 'completed', tail: text.slice(-2_000) }));
  }
  console.log(JSON.stringify({ ready: true, title: await window.title(), url: window.url() }));

  await new Promise((resolve) => {
    process.once('SIGINT', resolve);
    process.once('SIGTERM', resolve);
  });
  await app.close();
}

main().catch((error) => {
  console.error(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }));
  process.exitCode = 1;
});
