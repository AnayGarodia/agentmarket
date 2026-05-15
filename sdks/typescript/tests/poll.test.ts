import { test } from "node:test";
import assert from "node:assert/strict";

import {
  AgentmarketApiError,
  AzteaClient,
  ClarificationNeededError,
  JobFailedError,
} from "../src/index";
import { jobResponse, makeStubFetch } from "./helpers";

const BASE_URL = "http://localhost:8000";

function makeClient(stub: ReturnType<typeof makeStubFetch>) {
  return new AzteaClient({ baseUrl: BASE_URL, apiKey: "az_test", fetchFn: stub.fetch });
}

test("pollToCompletion returns terminal job on `complete`", async () => {
  const stub = makeStubFetch({
    routes: {
      "GET /jobs/job_a": ({ callIndexForRoute }) => ({
        body: jobResponse({
          job_id: "job_a",
          status: callIndexForRoute < 2 ? "running" : "complete",
          output_payload: callIndexForRoute < 2 ? null : { ok: true },
        }),
      }),
    },
  });
  const client = makeClient(stub);
  const result = await client.jobs.pollToCompletion("job_a", { pollIntervalMs: 1, timeoutSeconds: 5 });
  assert.equal(result.status, "complete");
  assert.deepEqual((result as { output_payload?: unknown }).output_payload, { ok: true });
  assert.equal(stub.callsFor("GET", "/jobs/job_a").length, 3);
});

test("pollToCompletion treats `stopped` as terminal success", async () => {
  const stub = makeStubFetch({
    routes: {
      "GET /jobs/job_s": { body: jobResponse({ job_id: "job_s", status: "stopped" }) },
    },
  });
  const client = makeClient(stub);
  const result = await client.jobs.pollToCompletion("job_s", { pollIntervalMs: 1 });
  assert.equal(result.status, "stopped");
});

test("pollToCompletion throws JobFailedError on `failed`", async () => {
  const stub = makeStubFetch({
    routes: {
      "GET /jobs/job_f": {
        body: jobResponse({
          job_id: "job_f",
          status: "failed",
          error_message: "handler raised",
          output_payload: { partial: 1 },
        }),
      },
    },
  });
  const client = makeClient(stub);
  await assert.rejects(
    () => client.jobs.pollToCompletion("job_f", { pollIntervalMs: 1 }),
    (err: unknown) => {
      assert.ok(err instanceof JobFailedError, "expected JobFailedError");
      assert.equal(err.message, "handler raised");
      assert.equal(err.jobId, "job_f");
      assert.deepEqual(err.output, { partial: 1 });
      return true;
    },
  );
});

test("pollToCompletion throws AgentmarketApiError(408) on timeout", async () => {
  const stub = makeStubFetch({
    routes: {
      "GET /jobs/job_t": { body: jobResponse({ job_id: "job_t", status: "running" }) },
    },
  });
  const client = makeClient(stub);
  await assert.rejects(
    () => client.jobs.pollToCompletion("job_t", { pollIntervalMs: 5, timeoutSeconds: 0 }),
    (err: unknown) => {
      assert.ok(err instanceof AgentmarketApiError, "expected AgentmarketApiError");
      assert.equal(err.status, 408);
      return true;
    },
  );
});

test("pollToCompletion throws ClarificationNeededError when no callback provided", async () => {
  const stub = makeStubFetch({
    routes: {
      "GET /jobs/job_c": { body: jobResponse({ job_id: "job_c", status: "awaiting_clarification" }) },
      "GET /jobs/job_c/messages": {
        body: {
          messages: [
            {
              message_id: 1,
              type: "clarification_request",
              payload: { question: "what region?" },
            },
          ],
        },
      },
    },
  });
  const client = makeClient(stub);
  await assert.rejects(
    () => client.jobs.pollToCompletion("job_c", { pollIntervalMs: 1, timeoutSeconds: 5 }),
    (err: unknown) => {
      assert.ok(err instanceof ClarificationNeededError, "expected ClarificationNeededError");
      assert.equal(err.question, "what region?");
      assert.equal(err.jobId, "job_c");
      return true;
    },
  );
});

test("pollToCompletion answers clarification via callback and resumes polling", async () => {
  let statusSequence = ["awaiting_clarification", "running", "complete"];
  const stub = makeStubFetch({
    routes: {
      "GET /jobs/job_q": () => ({
        body: jobResponse({
          job_id: "job_q",
          status: statusSequence.shift() ?? "complete",
          output_payload: { final: true },
        }),
      }),
      "GET /jobs/job_q/messages": {
        body: {
          messages: [
            { message_id: 1, type: "clarification_request", payload: { question: "city?" } },
          ],
        },
      },
      "POST /jobs/job_q/messages": ({ body }) => ({
        body: { message_id: 2, job_id: "job_q", type: "clarification_response", payload: body },
      }),
    },
  });
  const client = makeClient(stub);
  let askedWith: string | null = null;
  const result = await client.jobs.pollToCompletion("job_q", {
    pollIntervalMs: 1,
    timeoutSeconds: 5,
    onClarificationRequest: async (question, jobId) => {
      askedWith = `${jobId}:${question}`;
      return "SF";
    },
  });
  assert.equal(askedWith, "job_q:city?");
  assert.equal(result.status, "complete");
  const posted = stub.callsFor("POST", "/jobs/job_q/messages");
  assert.equal(posted.length, 1);
  assert.deepEqual(posted[0]?.body, { type: "clarification_response", payload: { answer: "SF" } });
});
