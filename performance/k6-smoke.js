import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  scenarios: {
    smoke: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 10),
      duration: __ENV.DURATION || "1m",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{type:fast}": ["p(95)<800"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const ADMIN_EMAIL = __ENV.ADMIN_EMAIL || "admin@example.com";
const ADMIN_PASSWORD = __ENV.ADMIN_PASSWORD || "ChangeMe123!";

function login() {
  const res = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ email: ADMIN_EMAIL, password: ADMIN_PASSWORD }),
    { headers: { "Content-Type": "application/json" }, tags: { type: "fast" } },
  );
  check(res, { "login 200": (r) => r.status === 200 });
  return res.json("access_token");
}

export default function () {
  const token = login();
  const headers = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };

  const health = http.get(`${BASE_URL}/api/health`, { tags: { type: "fast" } });
  check(health, { "health ok": (r) => r.status === 200 });

  const kbs = http.get(`${BASE_URL}/api/knowledge-bases`, {
    headers,
    tags: { type: "fast" },
  });
  check(kbs, { "list kbs 200": (r) => r.status === 200 });

  sleep(1);
}
