/**
 * Bybit 自动登录（Ant Browser「自动化脚本」版）
 *
 * 用法：Ant → 自动化脚本 → 导入本地文件 → 选这个 .cjs → 选中实例 → 运行。
 * 运行参数（params）填 JSON：
 *   { "email": "xxx@xxx.com", "password": "xxxx" }
 *
 * 入口约定来自 Ant 的 runner：module.exports.run(api)，api 里有 useBrowser / params / log。
 * useBrowser 返回 Playwright 的 { browser, context, page }。
 */
const LOGIN_URL = 'https://www.bybitglobal.com/zh-MY/login'
  + '?source=homepage&page_section=regularbanner&button_name=signup&abtest=undefined&regPhone=';
const AFTER_URL = 'https://www.bybitglobal.com/zh-MY/cards/';

async function clickByText(page, text) {
  const byRole = page.getByRole('button', { name: text });
  if (await byRole.count().catch(() => 0)) {
    await byRole.first().click({ timeout: 15000 });
    return true;
  }
  const byText = page.locator(`button:has-text("${text}")`);
  if (await byText.count().catch(() => 0)) {
    await byText.first().click({ timeout: 15000 });
    return true;
  }
  return false;
}

module.exports = {
  async run(api) {
    const { useBrowser, params, log } = api;
    const p = params || {};
    const email = String(p.email || '').trim();
    const password = String(p.password || '');
    if (!email || !password) {
      return { ok: false, summary: '运行参数里要填 {"email":"...","password":"..."}' };
    }

    const { page } = await useBrowser({
      url: LOGIN_URL,
      waitUntil: 'domcontentloaded',
      timeoutMs: 60000,
    });
    log('已打开登录页：' + page.url());

    // 已经登录过：直接返回
    if (!String(page.url()).includes('/login')) {
      return { ok: true, summary: '实例已是登录态：' + page.url() };
    }

    await page.waitForSelector('input[name=email]', { timeout: 40000 });
    await page.fill('input[name=email]', email);
    await page.waitForTimeout(400);
    if (!(await clickByText(page, '下一步'))) {
      return { ok: false, summary: '找不到「下一步」按钮，页面可能改版' };
    }

    // 有的账号这一步会弹验证码/通行密钥，不出现密码框
    try {
      await page.waitForSelector('input[name=password]', { timeout: 30000 });
    } catch (e) {
      return { ok: false, summary: '没等到密码框（可能要邮箱验证码或人机验证，需要人工）' };
    }

    await page.fill('input[name=password]', password);
    await page.waitForTimeout(400);
    if (!(await clickByText(page, '登录'))) {
      return { ok: false, summary: '找不到「登录」按钮' };
    }
    log('已提交登录，等待跳转');

    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      await page.waitForTimeout(1500);
      const url = String(page.url());
      if (!url.includes('/login')) break;
      const captcha = await page
        .locator('[class*=tcaptcha],[class*=captcha],[class*=geetest]')
        .first()
        .isVisible()
        .catch(() => false);
      if (captcha) {
        return { ok: false, summary: '出现滑块/人机验证，需要人工拖一下' };
      }
    }
    if (String(page.url()).includes('/login')) {
      return { ok: false, summary: '超时：点了登录但没跳走（网络慢或被风控静默拒绝）' };
    }
    log('登录成功：' + page.url());

    // 关「防钓鱼码」浮层（有就关，没有就跳过）
    await page.waitForTimeout(2500);
    try {
      const box = page.locator('div', { hasText: '防钓鱼码' })
        .filter({ has: page.locator('[class*=close],[aria-label*=lose]') })
        .first();
      await box.locator('[class*=close],[aria-label*=lose]').first().click({ timeout: 3000 });
      log('已关闭防钓鱼码弹窗');
    } catch (e) {
      log('没有防钓鱼码弹窗，跳过');
    }

    await page.goto(AFTER_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
    return { ok: true, summary: '登录成功并打开 ' + AFTER_URL };
  },
};
