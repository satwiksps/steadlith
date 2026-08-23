import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

const websiteDirectory = fileURLToPath(new URL("../", import.meta.url));
const nextCli = fileURLToPath(new URL("../node_modules/next/dist/bin/next", import.meta.url));

function reservePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      if (!address || typeof address === "string") {
        probe.close();
        reject(new Error("could not allocate a local smoke-test port"));
        return;
      }
      probe.close((error) => (error ? reject(error) : resolve(address.port)));
    });
  });
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitUntilReady(baseUrl, processHandle) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (processHandle.exitCode !== null) {
      throw new Error(`production server exited with code ${processHandle.exitCode}`);
    }
    try {
      const response = await fetch(baseUrl, { signal: AbortSignal.timeout(1_000) });
      if (response.status === 200) {
        return;
      }
    } catch {
      // The server is still starting.
    }
    await delay(100);
  }
  throw new Error("production server did not become ready within 10 seconds");
}

async function fetchRoute(baseUrl, path, expectedStatus) {
  const response = await fetch(new URL(path, baseUrl), {
    redirect: "manual",
    signal: AbortSignal.timeout(5_000),
  });
  if (response.status !== expectedStatus) {
    throw new Error(`${path} returned ${response.status}; expected ${expectedStatus}`);
  }
  return response;
}

function requireMatch(value, pattern, label) {
  if (!pattern.test(value)) {
    throw new Error(`${label} did not match ${pattern}`);
  }
}

async function stop(processHandle) {
  if (processHandle.exitCode !== null) {
    return;
  }
  const exited = once(processHandle, "exit");
  processHandle.kill();
  await Promise.race([exited, delay(5_000)]);
  if (processHandle.exitCode === null) {
    processHandle.kill("SIGKILL");
    await exited;
  }
}

const port = await reservePort();
const baseUrl = new URL(`http://127.0.0.1:${port}`);
const output = [];
const productionServer = spawn(
  process.execPath,
  [nextCli, "start", "--hostname", "127.0.0.1", "--port", String(port)],
  { cwd: websiteDirectory, env: process.env, stdio: ["ignore", "pipe", "pipe"] },
);
productionServer.stdout.on("data", (chunk) => output.push(chunk.toString()));
productionServer.stderr.on("data", (chunk) => output.push(chunk.toString()));

try {
  await waitUntilReady(baseUrl, productionServer);

  const home = await fetchRoute(baseUrl, "/", 200);
  const homeBody = await home.text();
  requireMatch(homeBody, /<title>Steadlith<\/title>/, "home page title");
  requireMatch(
    homeBody,
    /<h1[^>]*>[\s\S]*Index only what changed\.[\s\S]*Reuse everything else\.[\s\S]*<\/h1>/,
    "home page heading",
  );
  requireMatch(homeBody, /<main id="main">/, "home page main landmark");
  requireMatch(homeBody, /Python CLI \+ library/, "product type");
  requireMatch(homeBody, /What the plan means/, "plan explanation");
  requireMatch(homeBody, /Steadlith indexing flow/, "indexing flow");
  if (home.headers.get("x-content-type-options") !== "nosniff") {
    throw new Error("home page is missing the configured X-Content-Type-Options header");
  }

  const robots = await fetchRoute(baseUrl, "/robots.txt", 200);
  requireMatch(await robots.text(), /user-agent:\s*\*/i, "robots route");

  const sitemap = await fetchRoute(baseUrl, "/sitemap.xml", 200);
  requireMatch(await sitemap.text(), /<urlset(?:\s|>)/, "sitemap route");

  const icon = await fetchRoute(baseUrl, "/icon.svg", 200);
  requireMatch(icon.headers.get("content-type") ?? "", /^image\/svg\+xml/i, "icon content type");

  const socialCard = await fetchRoute(baseUrl, "/steadlith-social-card.jpg", 200);
  requireMatch(socialCard.headers.get("content-type") ?? "", /^image\/jpeg/i, "social card");

  await fetchRoute(baseUrl, "/missing-smoke-test-route", 404);
  process.stdout.write("Production website smoke test passed.\n");
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  if (output.length) {
    process.stderr.write(`Production server output:\n${output.join("")}\n`);
  }
  process.exitCode = 1;
} finally {
  await stop(productionServer);
}
