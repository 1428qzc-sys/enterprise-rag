import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

interface E2EDocument {
  id: string;
  name: string;
  status: string;
  version: number;
  progress: number;
  consistency_status: string;
}

interface E2EChunk {
  id: string;
  content: string;
  is_active: boolean;
}

const baseURL = process.env.E2E_BASE_URL || "http://127.0.0.1:19020";
const tenantSlug = process.env.E2E_TENANT_SLUG || "demo";
const email = process.env.E2E_EMAIL || "admin@example.com";
const password = process.env.E2E_PASSWORD || "ChangeMe123!";
const evalFixtureRoot = path.resolve(process.cwd(), "../backend/scripts/eval_fixture");
const releaseFixtureRoot = path.resolve(process.cwd(), "../backend/scripts/release_fixture");

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

async function waitForDocument(
  page: Page,
  token: string,
  kbId: string,
  expectedVersion: number,
): Promise<E2EDocument> {
  let current: E2EDocument | undefined;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${baseURL}/api/knowledge-bases/${kbId}/documents`,
          { headers: authHeaders(token) },
        );
        if (!response.ok()) return `http:${response.status()}`;
        const documents = (await response.json()) as E2EDocument[];
        current = documents[0];
        if (!current) return "missing";
        return [
          current.version,
          current.status,
          current.consistency_status,
          current.progress,
        ].join(":");
      },
      {
        message: `等待文档 v${expectedVersion} 入库并完成数据/向量一致性切换`,
        timeout: 90_000,
        intervals: [500, 1_000, 2_000],
      },
    )
    .toBe(`${expectedVersion}:done:consistent:100`);
  return current!;
}

async function assertNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return Math.max(0, root.scrollWidth - root.clientWidth);
  });
  expect(overflow, "页面不应出现横向滚动").toBeLessThanOrEqual(1);
}

test("租户管理员完成版本化 RAG 长链路并验证引用定位", async ({ page }, testInfo) => {
  const browserErrors: string[] = [];
  const failedApiRequests: string[] = [];
  let token = "";
  let kbId = "";

  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => browserErrors.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 500 && new URL(response.url()).pathname.startsWith("/api/")) {
      failedApiRequests.push(`HTTP ${response.status()} ${response.url()}`);
    }
  });
  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText || "unknown";
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/api/") && !failure.includes("ERR_ABORTED")) {
      failedApiRequests.push(`${failure} ${request.url()}`);
    }
  });

  const suffix = `${Date.now()}-${testInfo.workerIndex}`;
  const kbName = `E2E 验收知识库 ${suffix}`;
  const v1Fixture = path.join(evalFixtureRoot, "hr_policy.md");
  const v2Fixture = path.join(releaseFixtureRoot, "hr_policy_v2.md");

  try {
    await page.goto("/");
    await page.getByLabel("租户标识").fill(tenantSlug);
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByRole("button", { name: /新建知识库/ }).first()).toBeVisible();
    token = await page.evaluate(
      () => localStorage.getItem("enterprise-rag.access-token") || "",
    );
    expect(token, "登录后必须保存访问令牌").not.toBe("");

    await page.getByRole("button", { name: /新建知识库/ }).first().click();
    const createDialog = page.getByRole("dialog", { name: "新建知识库" });
    await createDialog.getByLabel("名称").fill(kbName);
    await createDialog
      .getByLabel("描述（可选）")
      .fill("Playwright 发布候选端到端验收数据");
    await createDialog.getByRole("button", { name: "创建", exact: true }).click();
    await expect(page).toHaveURL(/\/kb\/[^/]+\/documents$/);
    kbId = new URL(page.url()).pathname.split("/")[2] || "";
    expect(kbId).not.toBe("");

    await page.locator('input[type="file"][multiple]').setInputFiles(v1Fixture);
    const v1 = await waitForDocument(page, token, kbId, 1);
    const documentRow = page.locator(".document-row").first();
    await expect(documentRow).toContainText("v1");
    await expect(documentRow).toContainText("已就绪");
    await expect(documentRow).toContainText("数据一致");
    await expect(documentRow).toContainText("100%");

    await page.getByRole("link", { name: /智能问答/ }).click();
    await page.getByLabel("问题").fill("正式员工每个自然年度有多少天带薪年假？");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const firstAnswer = page.locator(".msg.assistant").last();
    await expect(firstAnswer.locator(".bubble")).toContainText("18 天", { timeout: 90_000 });
    await expect(firstAnswer.locator(".bubble")).toContainText("[1]");
    const sourceToggle = firstAnswer.getByRole("button", { name: /引用来源 \d+ 条/ });
    await expect(sourceToggle).toBeVisible();
    await expect(sourceToggle).toHaveAttribute("aria-expanded", /true|false/);
    const sourceLocator = firstAnswer.getByTitle("在文档原文中定位").first();
    if (!(await sourceLocator.isVisible())) await sourceToggle.click();
    await expect(sourceLocator).toBeVisible();
    await expect(firstAnswer.locator(".source").first()).toContainText("18 天");
    await sourceLocator.click();
    await expect(page).toHaveURL(/\/documents\?document=[^&]+&chunk=[^&]+$/);
    const highlightedChunk = page.locator(".chunk-row.highlighted");
    await expect(highlightedChunk).toBeVisible();
    await expect(highlightedChunk).toContainText("18 天");

    const fileChooserPromise = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: /新文件版本/ }).click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(v2Fixture);
    const v2 = await waitForDocument(page, token, kbId, 2);
    expect(v2.id).toBe(v1.id);
    await expect(page.locator(".document-row").first()).toContainText("v2");
    await expect(page.locator(".document-row").first()).toContainText("数据一致");

    const chunksResponse = await page.request.get(
      `${baseURL}/api/knowledge-bases/${kbId}/documents/${v2.id}/chunks`,
      { headers: authHeaders(token) },
    );
    expect(chunksResponse.ok()).toBeTruthy();
    const activeChunks = (await chunksResponse.json()) as E2EChunk[];
    const activeText = activeChunks
      .filter((chunk) => chunk.is_active)
      .map((chunk) => chunk.content)
      .join("\n");
    expect(activeText).toContain("20 天");
    expect(activeText).not.toContain("18 天");

    await page.getByRole("link", { name: /智能问答/ }).click();
    await page.getByLabel("问题").fill("2026 年 7 月起，正式员工每年有多少天带薪年假？");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const secondAnswer = page.locator(".msg.assistant").last();
    await expect(secondAnswer.locator(".bubble")).toContainText("20 天", { timeout: 90_000 });
    await expect(secondAnswer.locator(".bubble")).not.toContainText("18 天");
    await expect(secondAnswer.getByTitle("在文档原文中定位").first()).toHaveText("[1]");
    await expect(page.locator(".toast")).toBeHidden();

    const captureDir = process.env.E2E_CAPTURE_DIR;
    const viewports = [
      { width: 1440, height: 900, name: "chat-1440x900.png" },
      { width: 768, height: 1024, name: "chat-768x1024.png" },
      { width: 375, height: 812, name: "chat-375x812.png" },
    ];
    if (captureDir) mkdirSync(path.resolve(captureDir), { recursive: true });
    for (const viewport of viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      if (viewport.width === 375) {
        const expandedSources = page.locator(".sources-toggle[aria-expanded='true']");
        while ((await expandedSources.count()) > 0) await expandedSources.first().click();
        await page.locator(".msg.user").last().evaluate((element) => {
          element.scrollIntoView({ block: "start" });
        });
      } else {
        await page.locator(".messages").evaluate((element) => {
          element.scrollTop = element.scrollHeight;
        });
      }
      await assertNoHorizontalOverflow(page);
      if (captureDir) {
        await page.screenshot({
          path: path.join(path.resolve(captureDir), viewport.name),
          animations: "disabled",
        });
      }
    }

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole("link", { name: "租户管理" }).click();
    await expect(page.getByRole("heading", { name: "租户管理" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "用户", exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "角色与权限" }).click();
    await expect(page.getByRole("heading", { name: "角色与权限" })).toBeVisible();
    await page.getByRole("tab", { name: "审计记录" }).click();
    await expect(page.getByRole("heading", { name: "审计记录" })).toBeVisible();

    expect(browserErrors, "浏览器控制台和页面运行时不应报错").toEqual([]);
    expect(failedApiRequests, "核心 E2E 不应出现失败或 5xx API 请求").toEqual([]);
  } finally {
    if (kbId && token) {
      const cleanup = await page.request.delete(`${baseURL}/api/knowledge-bases/${kbId}`, {
        headers: authHeaders(token),
      });
      expect(
        [200, 204, 404],
        `清理 E2E 知识库失败：HTTP ${cleanup.status()}`,
      ).toContain(cleanup.status());
    }
  }
});
