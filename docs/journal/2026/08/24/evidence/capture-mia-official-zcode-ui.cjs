#!/usr/bin/env node
'use strict';

const path = require('node:path');
const { _electron: electron } = require(process.env.PLAYWRIGHT_CORE_PATH);

async function main() {
  const appRoot = process.env.ZCODE_APP_ROOT;
  const screenshotPath = process.env.ZCODE_SCREENSHOT_PATH;
  if (!appRoot || !screenshotPath) throw new Error('Missing required diagnostic environment.');

  const app = await electron.launch({
    executablePath: path.join(appRoot, 'zcode'),
    args: ['--no-sandbox', '--disable-gpu'],
    env: {
      ...process.env,
      APPDIR: appRoot,
    },
    timeout: 30_000,
  });
  try {
    let window = await app.firstWindow({ timeout: 30_000 });
    await window.waitForTimeout(10_000);
    const windowSnapshots = [];
    for (const candidate of app.windows()) {
      const bodyText = await candidate.locator('body').innerText().catch(() => '');
      windowSnapshots.push({ title: await candidate.title(), url: candidate.url(), bodyLength: bodyText.length });
      if (bodyText.length > (await window.locator('body').innerText().catch(() => '')).length) window = candidate;
    }
    console.log(JSON.stringify({ windowSnapshots }));
    if (process.env.ZCODE_INSPECT_PROJECT_MENU === '1') {
      const back = window.getByText('Back to workspace', { exact: true });
      if (await back.count()) {
        await back.first().click();
        await window.waitForTimeout(3_000);
      }
      const chooser = window.getByRole('button', { name: /choose workspace/i });
      if (await chooser.count()) {
        await chooser.first().click();
        await window.waitForTimeout(2_000);
      }
      console.log(JSON.stringify({ projectMenuText: (await window.locator('body').innerText()).slice(0, 12_000) }));
      if (process.env.ZCODE_SEND_PROBE === '1') {
        const outside = window.getByText('Work outside a project', { exact: true });
        if (await outside.count()) {
          await outside.first().click();
          await window.waitForTimeout(4_000);
        }
        const editor = window.locator('[contenteditable="true"], textarea').first();
        await editor.fill('Reply with exactly OK.');
        await window.getByRole('button', { name: 'Send', exact: true }).click();
        await window.waitForTimeout(60_000);
        console.log(JSON.stringify({ probeText: (await window.locator('body').innerText()).slice(-12_000) }));
      }
    }
    const buttons = await window.locator('button').evaluateAll((items) => items.map((item) => ({
      ariaLabel: item.getAttribute('aria-label'),
      text: item.textContent?.trim() || '',
      title: item.getAttribute('title'),
    })).filter((item) => item.ariaLabel || item.text || item.title));
    console.log(JSON.stringify({ buttons }));
    if (process.env.ZCODE_OPEN_SETTINGS === '1') {
      const candidates = [
        window.getByRole('button', { name: /settings/i }),
        window.locator('button[aria-label*="setting" i]'),
        window.locator('button[title*="setting" i]'),
      ];
      let clicked = false;
      for (const candidate of candidates) {
        if (await candidate.count()) {
          await candidate.first().click();
          clicked = true;
          break;
        }
      }
      console.log(JSON.stringify({ settingsClicked: clicked }));
      await window.waitForTimeout(5_000);
      if (process.env.ZCODE_OPEN_MODEL_SETTINGS === '1') {
        const modelSettings = window.getByText('Model settings', { exact: true });
        if (await modelSettings.count()) {
          await modelSettings.first().click();
          await window.waitForTimeout(7_000);
        }
      }
      console.log(JSON.stringify({ visibleText: (await window.locator('body').innerText()).slice(0, 12_000) }));
    }
    await window.screenshot({ path: screenshotPath, fullPage: true });
    console.log(JSON.stringify({
      title: await window.title(),
      url: window.url(),
      screenshotPath,
    }));
  } finally {
    await app.close();
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }));
  process.exitCode = 1;
});
