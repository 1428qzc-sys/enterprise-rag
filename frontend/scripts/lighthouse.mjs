import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { launch } from "chrome-launcher";
import lighthouse from "lighthouse";
import { chromium } from "playwright";

const url = process.env.LIGHTHOUSE_URL || "http://127.0.0.1:19020";
const reportPath = path.resolve(
  process.env.LIGHTHOUSE_REPORT || "../artifacts/lighthouse.json",
);
const thresholds = {
  performance: Number(process.env.LIGHTHOUSE_MIN_PERFORMANCE || "0.85"),
  accessibility: Number(process.env.LIGHTHOUSE_MIN_ACCESSIBILITY || "0.90"),
  "best-practices": Number(process.env.LIGHTHOUSE_MIN_BEST_PRACTICES || "0.90"),
};

const chrome = await launch({
  chromePath: chromium.executablePath(),
  chromeFlags: ["--headless=new", "--no-sandbox", "--disable-gpu"],
  logLevel: "silent",
});

try {
  const result = await lighthouse(url, {
    port: chrome.port,
    logLevel: "error",
    output: "json",
    onlyCategories: Object.keys(thresholds),
    preset: "desktop",
  });
  if (!result) throw new Error("Lighthouse 未返回报告");

  const report = Array.isArray(result.report) ? result.report[0] : result.report;
  mkdirSync(path.dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, report, "utf8");

  const scores = Object.fromEntries(
    Object.keys(thresholds).map((category) => [
      category,
      result.lhr.categories[category]?.score ?? 0,
    ]),
  );
  const summary = Object.entries(scores)
    .map(([category, score]) => `${category}=${Math.round(score * 100)}`)
    .join(", ");
  console.log(`Lighthouse ${result.lhr.fetchTime}: ${summary}`);
  console.log(`Report: ${reportPath}`);

  const failures = Object.entries(thresholds)
    .filter(([category, threshold]) => scores[category] < threshold)
    .map(
      ([category, threshold]) =>
        `${category} ${Math.round(scores[category] * 100)} < ${Math.round(threshold * 100)}`,
    );
  if (failures.length) {
    throw new Error(`Lighthouse 阈值未通过：${failures.join("; ")}`);
  }
} finally {
  await chrome.kill();
}
