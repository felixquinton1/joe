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
  let startedPayload;
  let authorization;
  const server = http.createServer((request, response) => {
    authorization = request.headers.authorization;
    response.setHeader("Content-Type", "application/json");
    if (request.url === "/api/status") {
      response.end(JSON.stringify(status));
      return;
    }
    if (request.url === "/api/conversations" && request.method === "GET") {
      response.end(JSON.stringify([{ id: "one", title: "Test" }]));
      return;
    }
    if (request.url === "/api/runs/active") {
      response.end(JSON.stringify([]));
      return;
    }
    if (request.url === "/api/conversations/one") {
      response.end(JSON.stringify({
        id: "one",
        title: "Test",
        messages: [{ role: "user", content: "Bonjour" }],
      }));
      return;
    }
    if (request.url === "/api/conversations" && request.method === "POST") {
      response.statusCode = 201;
      response.end(JSON.stringify({ id: "new", title: "Nouvelle conversation" }));
      return;
    }
    if (request.url === "/api/runs" && request.method === "POST") {
      let body = "";
      request.on("data", chunk => {
        body += chunk;
      });
      request.on("end", () => {
        startedPayload = JSON.parse(body);
        if (startedPayload.conversation_id === "conflict") {
          response.statusCode = 409;
          response.end(JSON.stringify({ error: "Conversation déjà occupée" }));
          return;
        }
        response.statusCode = 202;
        response.end(JSON.stringify({ run_id: "run-one" }));
      });
      return;
    }
    if (request.url === "/api/events/run-one?after=0") {
      response.setHeader("Content-Type", "text/event-stream");
      response.write(': keepalive\n\nid: 1\ndata: {"event_id":1,"type":"stream","text":"Bon"}\n\n');
      response.end('id: 2\ndata: {"event_id":2,"type":"complete","response":"Bonjour"}\n\n');
      return;
    }
    if (
      request.url === "/api/runs/run-one/cancel" &&
      request.method === "POST"
    ) {
      response.statusCode = 202;
      response.end(JSON.stringify({ cancelled: true }));
      return;
    }
    response.statusCode = 404;
    response.end(JSON.stringify({ error: "missing" }));
  });
  return new Promise(resolve => {
    server.listen(0, "127.0.0.1", () =>
      resolve({
        server,
        startedPayload: () => startedPayload,
        authorization: () => authorization,
      })
    );
  });
}

test("reads status and conversations from API 1.x", async t => {
  const fixtureServer = await fixture({
    version: "0.21.16",
    api_version: "1.0",
    project: "/tmp/project",
    providers: ["codex"],
    modes: ["fast"],
  });
  const { server } = fixtureServer;
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

test("sends the local bearer token and builds a pairing URL", async t => {
  const fixtureServer = await fixture({
    version: "0.23.1",
    api_version: "1.1",
    project: "/tmp/project",
    providers: [],
    modes: [],
  });
  const { server, authorization } = fixtureServer;
  t.after(() => server.close());
  const address = server.address();
  const client = new JoeClient(
    `http://127.0.0.1:${address.port}`,
    "test-token"
  );

  await client.conversations();
  assert.equal(authorization(), "Bearer test-token");
  assert.equal(
    new URL(client.browserUrl()).hash,
    "#token=test-token"
  );
});

test("rejects an incompatible API major", async t => {
  const fixtureServer = await fixture({
    version: "1.0.0",
    api_version: "2.0",
    project: "/tmp/project",
    providers: [],
    modes: [],
  });
  const { server } = fixtureServer;
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

test("creates a conversation, starts a safe run, reads SSE and cancels", async t => {
  const fixtureServer = await fixture({
    version: "0.21.19",
    api_version: "1.0",
    project: "/tmp/project",
    providers: ["codex"],
    modes: ["fast"],
  });
  const { server, startedPayload } = fixtureServer;
  t.after(() => server.close());
  const address = server.address();
  const client = new JoeClient(`http://127.0.0.1:${address.port}`);

  assert.equal((await client.conversation("one")).messages[0].content, "Bonjour");
  assert.equal((await client.createConversation()).id, "new");
  assert.equal(
    await client.startRun("one", "Réponds", "read-only"),
    "run-one"
  );
  assert.deepEqual(startedPayload(), {
    conversation_id: "one",
    request: "Réponds",
    execution_mode: "read-only",
  });
  const events = [];
  for await (const event of client.events("run-one")) {
    events.push(event);
  }
  assert.deepEqual(events.map(event => event.type), ["stream", "complete"]);
  assert.equal(await client.cancelRun("run-one"), true);
});

test("carries a server-side approval id and never self-approves", async t => {
  const fixtureServer = await fixture({
    version: "0.22.0",
    api_version: "1.0",
    project: "/tmp/project",
    providers: ["claude"],
    modes: ["fast"],
  });
  const { server, startedPayload } = fixtureServer;
  t.after(() => server.close());
  const address = server.address();
  const client = new JoeClient(`http://127.0.0.1:${address.port}`);

  assert.equal(
    await client.startRun("one", "Teste le paquet", undefined, "approval-42"),
    "run-one"
  );
  assert.equal(startedPayload().approval_id, "approval-42");
  assert.equal(startedPayload().full_access_approved, undefined);
});

test("keeps the HTTP status and server detail on an API conflict", async t => {
  const fixtureServer = await fixture({
    version: "0.21.20",
    api_version: "1.0",
    project: "/tmp/project",
    providers: [],
    modes: [],
  });
  const { server } = fixtureServer;
  t.after(() => server.close());
  const address = server.address();
  const client = new JoeClient(`http://127.0.0.1:${address.port}`);

  await assert.rejects(
    () => client.startRun("conflict", "Encore"),
    error => error.status === 409 && error.message === "Conversation déjà occupée"
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
