import assert from "node:assert/strict";
import test from "node:test";

import {
  chooseRunToResume,
  completedResponse,
  MAX_PERSISTED_STREAM_CHARACTERS,
  updateBookmark,
} from "../out/runRecovery.js";

test("resumes a persisted run before another active run", () => {
  const bookmark = {
    runId: "remembered",
    conversationId: "one",
    request: "Continue",
    lastEventId: 4,
    streamedText: "déjà reçu",
  };

  assert.equal(
    chooseRunToResume(
      [
        {
          run_id: "other",
          conversation_id: "two",
          request: "Autre",
        },
      ],
      bookmark,
      "two"
    ),
    bookmark
  );
});

test("attaches the active run from the selected conversation", () => {
  const chosen = chooseRunToResume(
    [
      { run_id: "first", conversation_id: "one", request: "Un" },
      { run_id: "second", conversation_id: "two", request: "Deux" },
    ],
    undefined,
    "two"
  );

  assert.deepEqual(chosen, {
    runId: "second",
    conversationId: "two",
    request: "Deux",
    lastEventId: 0,
    streamedText: "",
  });
});

test("persists the cursor and bounds streamed output", () => {
  const bookmark = {
    runId: "run",
    conversationId: "one",
    request: "Long",
    lastEventId: 2,
    streamedText: "",
  };
  const text = `début${"x".repeat(MAX_PERSISTED_STREAM_CHARACTERS)}`;
  const updated = updateBookmark(
    bookmark,
    { event_id: 3, type: "stream", text: "x" },
    text
  );

  assert.equal(updated.lastEventId, 3);
  assert.equal(
    updated.streamedText.length,
    MAX_PERSISTED_STREAM_CHARACTERS
  );
  assert.equal(updated.streamTruncated, true);
  assert.equal(updated.streamedText.startsWith("début"), false);
});

test("finds a completed response after an in-memory run expires", () => {
  assert.equal(
    completedResponse(
      {
        id: "one",
        messages: [
          { role: "assistant", content: "Ancien", run_id: "old" },
          { role: "assistant", content: "Terminé", run_id: "target" },
        ],
      },
      "target"
    ),
    "Terminé"
  );
});
