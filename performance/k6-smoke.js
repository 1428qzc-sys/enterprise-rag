import http from "k6/http";
import { check, fail, sleep } from "k6";
import { Counter } from "k6/metrics";

const FIXTURE = open("../backend/scripts/eval_fixture/hr_policy.md", "b");
const businessErrors = new Counter("business_errors");

export const options = {
  scenarios: {
    reads: {
      executor: "constant-vus",
      exec: "readScenario",
      vus: Number(__ENV.READ_VUS || 3),
      duration: __ENV.READ_DURATION || "20s",
      gracefulStop: "5s",
    },
    writes: {
      executor: "constant-vus",
      exec: "writeScenario",
      vus: Number(__ENV.WRITE_VUS || 1),
      duration: __ENV.WRITE_DURATION || "15s",
      startTime: __ENV.WRITE_START || "22s",
      gracefulStop: "5s",
    },
  },
  thresholds: {
    checks: ["rate==1"],
    http_req_failed: ["rate==0"],
    business_errors: ["count==0"],
    "http_req_duration{class:read}": ["p(95)<300"],
    "http_req_duration{class:retrieval}": ["p(95)<300"],
    "http_req_duration{class:write}": ["p(95)<800"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://host.docker.internal:19021";
const ADMIN_EMAIL = __ENV.ADMIN_EMAIL || "admin@example.com";
const ADMIN_PASSWORD = __ENV.ADMIN_PASSWORD || "ChangeMe123!";
const TENANT_SLUG = __ENV.TENANT_SLUG || "demo";
const THINK_TIME_SECONDS = Number(__ENV.THINK_TIME_SECONDS || 0.5);
const SUMMARY_PATH = __ENV.SUMMARY_PATH || "/workspace/artifacts/k6-summary.json";

function jsonHeaders(token) {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

function requireResponse(response, name, expectedStatuses) {
  const ok = check(response, {
    [`${name} status ${expectedStatuses.join("/")}`]: (item) =>
      expectedStatuses.includes(item.status),
  });
  if (!ok) {
    businessErrors.add(1, { operation: name });
  }
  return ok;
}

function login() {
  const response = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ tenant_slug: TENANT_SLUG, email: ADMIN_EMAIL, password: ADMIN_PASSWORD }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { class: "setup", operation: "login" },
    },
  );
  if (!requireResponse(response, "login", [200])) {
    fail(`login failed: ${response.status} ${response.body}`);
  }
  return response.json("access_token");
}

function waitForDocument(token, kbId, documentId) {
  const headers = jsonHeaders(token);
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const response = http.get(
      `${BASE_URL}/api/knowledge-bases/${kbId}/documents/${documentId}`,
      { headers, tags: { class: "setup", operation: "wait_document" } },
    );
    if (!requireResponse(response, "wait document", [200])) {
      fail(`document polling failed: ${response.status} ${response.body}`);
    }
    const payload = response.json();
    if (payload.status === "failed") {
      fail(`document ingestion failed: ${payload.error || "unknown"}`);
    }
    if (payload.status === "done" && payload.consistency_status === "consistent") {
      return;
    }
    sleep(0.25);
  }
  fail("document ingestion timed out");
}

export function setup() {
  const ready = http.get(`${BASE_URL}/api/health/ready`, {
    tags: { class: "setup", operation: "readiness" },
  });
  if (!requireResponse(ready, "readiness", [200])) {
    fail(`readiness failed: ${ready.status} ${ready.body}`);
  }

  const token = login();
  const headers = jsonHeaders(token);
  const create = http.post(
    `${BASE_URL}/api/knowledge-bases`,
    JSON.stringify({
      name: `Performance Fixture ${Date.now()}`,
      description: "k6 固定性能夹具；teardown 自动删除",
    }),
    { headers, tags: { class: "setup", operation: "create_fixture_kb" } },
  );
  if (!requireResponse(create, "create fixture kb", [201])) {
    fail(`fixture KB creation failed: ${create.status} ${create.body}`);
  }
  const kbId = create.json("id");

  const upload = http.post(
    `${BASE_URL}/api/knowledge-bases/${kbId}/documents/upload`,
    { file: http.file(FIXTURE, "hr_policy.md", "text/markdown") },
    {
      headers: { Authorization: `Bearer ${token}` },
      tags: { class: "setup", operation: "upload_fixture" },
    },
  );
  if (!requireResponse(upload, "upload fixture", [201])) {
    fail(`fixture upload failed: ${upload.status} ${upload.body}`);
  }
  const documentId = upload.json("id");
  waitForDocument(token, kbId, documentId);

  // 预建 KB 的 BM25 缓存；应用级 Jieba 预热已在 readiness 前完成。
  const warmRetrieval = http.post(
    `${BASE_URL}/api/retrieve`,
    JSON.stringify({ kb_id: kbId, query: "正式员工每年有多少天带薪年假？", top_k: 5 }),
    { headers, tags: { class: "setup", operation: "warm_retrieval" } },
  );
  if (!requireResponse(warmRetrieval, "warm retrieval", [200])) {
    fail(`retrieval warmup failed: ${warmRetrieval.status} ${warmRetrieval.body}`);
  }
  if ((warmRetrieval.json("results") || []).length === 0) {
    businessErrors.add(1, { operation: "warm_retrieval_empty" });
    fail("retrieval warmup returned no result");
  }

  return { token, kbId, documentId };
}

export function readScenario(data) {
  const headers = jsonHeaders(data.token);
  const list = http.get(`${BASE_URL}/api/knowledge-bases`, {
    headers,
    tags: { class: "read", operation: "list_kbs" },
  });
  requireResponse(list, "list kbs", [200]);

  const detail = http.get(`${BASE_URL}/api/knowledge-bases/${data.kbId}`, {
    headers,
    tags: { class: "read", operation: "kb_detail" },
  });
  requireResponse(detail, "kb detail", [200]);

  const documents = http.get(
    `${BASE_URL}/api/knowledge-bases/${data.kbId}/documents`,
    { headers, tags: { class: "read", operation: "list_documents" } },
  );
  requireResponse(documents, "list documents", [200]);

  const retrieval = http.post(
    `${BASE_URL}/api/retrieve`,
    JSON.stringify({
      kb_id: data.kbId,
      query: "正式员工每年有多少天带薪年假？",
      top_k: 5,
    }),
    { headers, tags: { class: "retrieval", operation: "hybrid_retrieval" } },
  );
  if (requireResponse(retrieval, "hybrid retrieval", [200])) {
    const results = retrieval.json("results") || [];
    if (results.length === 0 || !String(results[0].content || "").includes("18 天")) {
      businessErrors.add(1, { operation: "hybrid_retrieval_fact" });
      check(false, { "retrieval returns fixed fixture fact": () => false });
    } else {
      check(true, { "retrieval returns fixed fixture fact": () => true });
    }
  }
  sleep(THINK_TIME_SECONDS);
}

export function writeScenario(data) {
  const headers = jsonHeaders(data.token);
  const create = http.post(
    `${BASE_URL}/api/knowledge-bases`,
    JSON.stringify({
      name: `k6 write ${__VU}-${__ITER}-${Date.now()}`,
      description: "本地写延迟夹具；本轮立即删除",
    }),
    { headers, tags: { class: "write", operation: "create_kb" } },
  );
  if (requireResponse(create, "write create kb", [201])) {
    const kbId = create.json("id");
    const remove = http.del(`${BASE_URL}/api/knowledge-bases/${kbId}`, null, {
      headers,
      tags: { class: "write", operation: "delete_kb" },
    });
    requireResponse(remove, "write delete kb", [204]);
  }
  sleep(THINK_TIME_SECONDS);
}

export function teardown(data) {
  if (!data || !data.token || !data.kbId) {
    return;
  }
  const response = http.del(`${BASE_URL}/api/knowledge-bases/${data.kbId}`, null, {
    headers: jsonHeaders(data.token),
    tags: { class: "teardown", operation: "delete_fixture_kb" },
  });
  requireResponse(response, "teardown fixture", [204, 404]);
}

export function handleSummary(data) {
  const sanitized = { ...data };
  delete sanitized.setup_data;
  return {
    stdout: `Sanitized k6 summary: ${SUMMARY_PATH}\n`,
    [SUMMARY_PATH]: JSON.stringify(sanitized, null, 2),
  };
}
