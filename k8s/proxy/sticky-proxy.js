/*
 * The platform's session-affinity reverse proxy, the general version of the Phase 0 spike
 * (same core mechanism: watch a Service's Endpoints via the K8s API, route on an
 * HMAC-signed sticky cookie). Phase 5 adds multi-tenant routing: the path prefix
 * /t/{tenant_id}/... selects which tenant's namespace/Service to route to, instead of a
 * single hardcoded backend.
 *
 * Runs as its own platform-owned Deployment (k8s/bootstrap/reverse-proxy.yaml) with a
 * ClusterRole letting it read Endpoints in any tenant-* namespace, a broader grant than any
 * single tenant's own scoped RBAC (Phase 4): this is platform infrastructure routing
 * traffic for tenants, not something a tenant's own credentials could do.
 */
const http = require("http");
const https = require("https");
const crypto = require("crypto");
const fs = require("fs");

const K8S_API = "https://kubernetes.default.svc";
const TOKEN = fs.readFileSync("/var/run/secrets/kubernetes.io/serviceaccount/token", "utf8");
const CA = fs.readFileSync("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt");
const COOKIE_SECRET = process.env.PROXY_COOKIE_SECRET;
if (!COOKIE_SECRET) {
  throw new Error("PROXY_COOKIE_SECRET must be set");
}

const TENANT_APP_NAME = "tenant-app";
const TENANT_APP_PORT = 8080;
const TENANT_PATH_RE = /^\/t\/(\d+)(\/.*)?$/;

function sign(value) {
  const h = crypto.createHmac("sha256", COOKIE_SECRET).update(value).digest("hex");
  return `${value}.${h}`;
}
function verify(signed) {
  const idx = signed.lastIndexOf(".");
  if (idx === -1) return null;
  const value = signed.slice(0, idx);
  const sig = signed.slice(idx + 1);
  const expected = crypto.createHmac("sha256", COOKIE_SECRET).update(value).digest("hex");
  return sig === expected ? value : null;
}

function getEndpointIPs(namespace) {
  return new Promise((resolve, reject) => {
    const req = https.request(
      `${K8S_API}/api/v1/namespaces/${namespace}/endpoints/${TENANT_APP_NAME}`,
      { headers: { Authorization: `Bearer ${TOKEN}` }, ca: CA },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          if (res.statusCode === 404) return resolve([]); //no live deployment for this tenant yet
          try {
            const parsed = JSON.parse(data);
            const ips = [];
            for (const subset of parsed.subsets || []) {
              for (const addr of subset.addresses || []) ips.push(addr.ip);
            }
            resolve(ips);
          } catch (e) {
            reject(e);
          }
        });
      }
    );
    req.on("error", reject);
    req.end();
  });
}

function parseCookies(header) {
  const out = {};
  (header || "").split(";").forEach((p) => {
    const [k, ...v] = p.trim().split("=");
    if (k) out[k] = v.join("=");
  });
  return out;
}

const server = http.createServer(async (req, res) => {
  const match = TENANT_PATH_RE.exec(req.url);
  if (!match) {
    res.writeHead(404);
    res.end("expected path /t/<tenant_id>/...");
    return;
  }
  const tenantId = match[1];
  const namespace = `tenant-${tenantId}`;
  const forwardPath = match[2] || "/";
  const cookieName = `affinity_t${tenantId}`;

  try {
    const ips = await getEndpointIPs(namespace);
    if (ips.length === 0) {
      res.writeHead(503);
      res.end(`no running deployment for tenant ${tenantId}`);
      return;
    }

    const cookies = parseCookies(req.headers.cookie);
    let targetIP = null;
    let setCookie = false;

    if (cookies[cookieName]) {
      const verified = verify(cookies[cookieName]);
      if (verified && ips.includes(verified)) targetIP = verified;
    }
    if (!targetIP) {
      targetIP = ips[Math.floor(Math.random() * ips.length)];
      setCookie = true;
    }

    const proxyReq = http.request(
      { host: targetIP, port: TENANT_APP_PORT, path: forwardPath, method: req.method, headers: req.headers },
      (proxyRes) => {
        const headers = { ...proxyRes.headers, "x-routed-tenant": tenantId, "x-routed-to": targetIP };
        if (setCookie) headers["set-cookie"] = `${cookieName}=${sign(targetIP)}; Path=/t/${tenantId}`;
        res.writeHead(proxyRes.statusCode, headers);
        proxyRes.pipe(res);
      }
    );
    proxyReq.on("error", (e) => {
      res.writeHead(502);
      res.end(`upstream error: ${e.message}`);
    });
    req.pipe(proxyReq);
  } catch (e) {
    res.writeHead(500);
    res.end(`proxy error: ${e.message}`);
  }
});

server.listen(3000, () => console.log("multi-tenant sticky proxy listening on :3000"));
