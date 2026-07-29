import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { JoeApiError, JoeClient } from "../out/api.js";

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

  await assert.rejects(() => client.status(), JoeApiError);
});
