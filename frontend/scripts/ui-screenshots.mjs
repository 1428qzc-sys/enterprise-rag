import { chromium } from "playwright";
import { mkdirSync } from "fs";
import { join } from "path";

const BASE = process.env.ERAG_UI_BASE || "http://127.0.0.1:19020";
const OUT = join(process.cwd(), "..", "docs", "screenshots");
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });

async function shot(page, name) {
  await page.screenshot({ path: join(OUT, name), fullPage: true });
}

const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();

try {
  await page.goto(BASE, { waitUntil: "networkidle", timeout: 30000 });
  await shot(page, "01-login-light-after.png");

  await page.locator("#login-tenant").fill("demo");
  await page.locator('input[autocomplete="username"]').fill("admin@example.com");
  await page.locator('input[type="password"]').fill("ChangeMe123!");
  await page.locator("button.auth-submit").click();
  await page.waitForSelector(".dashboard-stats, .kb-grid, .empty.card, .state-banner.error", {
    timeout: 20000,
  });
  await page.waitForTimeout(1000);
  await shot(page, "02-dashboard-light-after.png");

  await page.locator('button[title*="深色"], button:has-text("🌙")').first().click();
  await page.waitForTimeout(400);
  await shot(page, "03-dashboard-dark-after.png");

  const kbLink = page.locator(".kb-card").first();
  if (await kbLink.count()) {
    await kbLink.click();
    await page.waitForTimeout(800);
    await page.locator('a:has-text("文档")').click();
    await page.waitForTimeout(1200);
    await shot(page, "04-documents-dark-after.png");

    await page.locator('a:has-text("问答")').click();
    await page.waitForTimeout(800);
    await shot(page, "05-chat-empty-after.png");

    const chatInput = page.locator(".composer textarea");
    await chatInput.fill("这份知识库主要讲什么？");
    await page.locator(".composer button:has-text('发送')").click();
    await page.waitForSelector(".sources-list .source, .sources-toggle", { timeout: 25000 });
    await page.waitForTimeout(1500);
    await shot(page, "06-chat-citations-after.png");
  }

  console.log(JSON.stringify({ ok: true, out: OUT, base: BASE }));
} catch (err) {
  console.error(err);
  await shot(page, "99-error.png").catch(() => {});
  process.exit(1);
} finally {
  await browser.close();
}
