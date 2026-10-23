const {chromium, expect} = require('playwright/test');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.CSLURM_BROWSER_CHANNEL ? {channel: process.env.CSLURM_BROWSER_CHANNEL} : {})});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(process.env.CSLURM_TEST_URL);
    await expect(page.locator('#stat-total')).toHaveText('3');
    await expect(page.locator('#workspace-select')).toHaveValue('local');
    await page.getByRole('button', {name: '管理服务器', exact: true}).click();
    await expect(page.locator('#server-ssh-options option[value="cluster"]')).toHaveCount(1);
    // A late response from a previous form must not populate the new form.
    let release;
    let started;
    const requested = new Promise(resolve => { started = resolve; });
    const held = new Promise(resolve => { release = resolve; });
    await page.route('**/api/servers/discover', async route => {
      if (route.request().postDataJSON().alias !== 'delayed') return route.continue();
      started();
      await held;
      await route.fulfill({json: {alias: 'delayed', status: 'ok', warnings: [], environments: [{
        python: '/stale/python', python_version: '3.12.0', installed: true, supported: true,
        web: true, package: '/stale/cslurm', version: '1', root: '/stale/data', root_source: 'default',
      }]}});
    });
    await page.locator('#server-alias').fill('delayed');
    await page.locator('#server-detect').click();
    await requested;
    await expect(page.locator('#server-save')).toBeDisabled();
    await page.locator('#server-new').click();
    const responded = page.waitForResponse(response => response.url().endsWith('/api/servers/discover'));
    release();
    await responded;
    await expect(page.locator('#server-python')).toHaveValue('python3');
    await expect(page.locator('#server-root')).toHaveValue('~/.cslurm');
    await expect(page.locator('#server-environment-label')).toBeHidden();
    await page.unroute('**/api/servers/discover');
    await page.locator('#server-name').fill('Test cluster');
    await page.locator('#server-alias').fill('cluster');
    await page.locator('#server-alias').press('Tab');
    await expect(page.locator('#server-environment')).toBeVisible();
    await expect(page.locator('#server-detection')).toContainText('CleverSlurm', {timeout: 60000});
    await page.locator('#server-advanced summary').click();
    await expect(page.locator('#server-python')).not.toHaveValue('python3');
    await page.locator('#server-root').fill(process.env.CSLURM_TEST_REMOTE);
    await page.locator('#server-detect').click();
    await expect(page.locator('#server-save')).toBeEnabled({timeout: 60000});
    await expect(page.locator('#server-root')).toHaveValue(process.env.CSLURM_TEST_REMOTE);
    await expect(page.locator('#server-detection')).toContainText('保留已填写值');
    await page.screenshot({path: path.join(process.env.CSLURM_SCREENSHOTS, 'discovery-desktop.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(process.env.CSLURM_SCREENSHOTS, 'discovery-mobile.png'), fullPage: true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.setViewportSize({width: 1440, height: 1000});
    await page.locator('#server-save').click();
    await page.getByRole('button', {name: '连接 Test cluster', exact: true}).click();
    await expect(page.getByRole('button', {name: '查看 Test cluster', exact: true})).toBeEnabled();
    await page.getByRole('button', {name: '查看 Test cluster', exact: true}).click();
    await expect(page.locator('#stat-total')).toHaveText('1');
    await expect(page.locator('#detail')).toContainText('#900');
    await expect(page.locator('#refresh')).toBeDisabled();
    await page.screenshot({path: path.join(process.env.CSLURM_SCREENSHOTS, 'remote-desktop.png'), fullPage: true});
    await page.locator('#workspace-select').selectOption('local');
    await expect(page.locator('#stat-total')).toHaveText('3');
    await expect(page.locator('#detail')).not.toContainText('#900');
    await page.getByRole('button', {name: '管理服务器', exact: true}).click();
    await page.locator('#server-name').fill('Fresh server');
    await page.locator('#server-alias').fill('missing');
    // Manual mode remains available even for configured aliases.
    await page.locator('#server-advanced summary').click();
    await expect(page.locator('#server-save')).toBeEnabled({timeout: 60000});
    await page.locator('#server-python').fill(process.env.CSLURM_TEST_PYTHON);
    await page.locator('#server-root').fill(process.env.CSLURM_TEST_NEW_ROOT);
    await page.locator('#server-install-dir').fill(process.env.CSLURM_TEST_INSTALL);
    await page.locator('#server-save').click();
    await page.getByRole('button', {name: '连接 Fresh server', exact: true}).click();
    await page.getByRole('button', {name: '安装', exact: true}).click();
    await page.locator('#server-install-confirm').fill('missing');
    await page.locator('#server-install').click();
    await expect(page.locator('#servers-list')).toContainText('已安装，等待连接', {timeout: 60000});
    await page.getByRole('button', {name: '连接 Fresh server', exact: true}).click();
    await expect(page.getByRole('button', {name: '查看 Fresh server', exact: true})).toBeEnabled();
    await page.getByRole('button', {name: '查看 Fresh server', exact: true}).click();
    await expect(page.locator('#stat-total')).toHaveText('0');
    await page.setViewportSize({width: 390, height: 844});
    await page.getByRole('button', {name: '管理服务器', exact: true}).click();
    await page.screenshot({path: path.join(process.env.CSLURM_SCREENSHOTS, 'servers-mobile.png'), fullPage: true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.locator('#servers-close').click();
    await page.locator('#workspace-select').selectOption('local');
    await expect(page.locator('#stat-total')).toHaveText('3');
    await page.screenshot({path: path.join(process.env.CSLURM_SCREENSHOTS, 'local-mobile.png'), fullPage: true});
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('Browser: local/remote switching, read-only gates, offline installation and mobile layout passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
