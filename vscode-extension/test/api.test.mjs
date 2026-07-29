import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import {
  JoeClient,
  JoeCompatibilityError,
  JoeConnectionError,
} from "../out/api.js";
import { planRestart, waitForJoe } from "../out/restart.js";

function fixture(status) {
  const server = http.createServer((request, response) => {
    response.setHeader("Content-Type", "application/json");
    if (request.url === "/api/status") {
      response.end(JSON.stringify(status));
      return;
    }
    if (request.url === "/api/conversations") {
      response.end(JSON.stringify([{ id: "one", title: "Test" }]));
      return;
    }
    if (request.url === "/api/runs/active") {
      response.end(JSON.stringify([]));
      return;
    }
    response.statusCode = 404;
    response.end(JSON.stringify({ error: "missing" }));
  });
  return new Promise(resolve => {
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

test("reads status and conversations from API 1.x", async t => {
  const server = await fixture({
    version: "0.21.16",
    api_version: "1.0",
    project: "/tmp/project",
    providers: ["codex"],
    modes: ["fast"],
  });
  t.after(() => server.close());
  const address = server.address();
  const client = new JoeClient(`http://127.0.0.1:${address.port}`);

  assert.equal((await client.status()).api_version, "1.0");
  assert.equal((await client.conversations())[0].title, "Test");
  assert.deepEqual(await client.activeRuns(), []);
  assert.deepEqual(
    await planRestart(client, "/tmp/fallback"),
    { mode: "ready", project: "/tmp/project" }
  );
});

test("rejects an incompatible API major", async t => {
  const server = await fixture({
    version: "1.0.0",
    api_version: "2.0",
    project: "/tmp/project",
    providers: [],
    modes: [],
  });
  t.after(() => server.close());
  const address = server.address();
  const client = new JoeClient(`http://127.0.0.1:${address.port}`);

  await assert.rejects(() => client.status(), JoeCompatibilityError);
  assert.deepEqual(
    await planRestart(client, "/tmp/fallback"),
    {
      mode: "error",
      message: "API Joe incompatible : 2.0",
    }
  );
});

test("only an unreachable server enables forced restart", async () => {
  const temporary = http.createServer();
  await new Promise(resolve => temporary.listen(0, "127.0.0.1", resolve));
  const address = temporary.address();
  await new Promise(resolve => temporary.close(resolve));
  const client = new JoeClient(`http://127.0.0.1:${address.port}`);

  await assert.rejects(() => client.status(), JoeConnectionError);
  assert.deepEqual(
    await planRestart(client, "/tmp/workspace"),
    { mode: "force", project: "/tmp/workspace" }
  );
});

test("waits until the restarted server actually answers", async () => {
  let attempts = 0;
  const status = {
    version: "0.21.19",
    api_version: "1.0",
    project: "/tmp/project",
    providers: [],
    modes: [],
  };
  const client = {
    async status() {
      attempts += 1;
      if (attempts < 3) {
        throw new JoeConnectionError("starting");
      }
      return status;
    },
  };

  assert.deepEqual(await waitForJoe(client, 4, 0), status);
  assert.equal(attempts, 3);
});
