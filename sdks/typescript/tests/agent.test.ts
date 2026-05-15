import { test } from "node:test";
import assert from "node:assert/strict";

import { AgentServer, AzteaClient, ClarificationNeeded } from "../src/index";
import { jobResponse, makeStubFetch } from "./helpers";

const BASE_URL = "http://localhost:8000";
const AGENT_ID = "00000000-0000-0000-0000-000000000001";

function makeClient(stub: ReturnType<typeof makeStubFetch>) {
  return new AzteaClient({ baseUrl: BASE_URL, apiKey: "az_worker", fetchFn: stub.fetch });
}

test("AgentServer claims a pending job, runs handler, and completes", async () => {
  let pendingDelivered = false;
  const stub = makeStubFetch({
    routes: {
      [`GET /jobs/agent/${AGENT_ID}`]: () => {
        if (pendingDelivered) return { body: { jobs: [] } };
        pendingDelivered = true;
        return {
          body: {
            jobs: [
              jobResponse({
                job_id: "job_x",
                agent_id: AGENT_ID,
                status: "pending",
                input_payload: { n: 2 },
              }),
            ],
          },
        };
      },
      "POST /jobs/job_x/claim": {
        body: jobResponse({
          job_id: "job_x",
          status: "claimed",
          claim_token: "ct-abc",
        } as Record<string, unknown>),
      },
      "POST /jobs/job_x/complete": ({ body }) => ({
        body: jobResponse({
          job_id: "job_x",
          status: "complete",
          output_payload: (body as Record<string, unknown>).output_payload ?? null,
        }),
      }),
    },
  });
  const client = makeClient(stub);
  let handlerInput: unknown = null;
  const server = new AgentServer({
    apiKey: "az_worker",
    agentId: AGENT_ID,
    client,
    pollIntervalMs: 5,
    heartbeatIntervalMs: 10_000,
    handler: (input) => {
      handlerInput = input;
      return { doubled: (input as { n: number }).n * 2 };
    },
  });
  const processed = await server.runOnce();
  assert.equal(processed, 1);
  assert.deepEqual(handlerInput, { n: 2 });
  const completes = stub.callsFor("POST", "/jobs/job_x/complete");
  assert.equal(completes.length, 1);
  assert.deepEqual(completes[0]?.body, {
    output_payload: { doubled: 4 },
    claim_token: "ct-abc",
  });
});

test("AgentServer fails the job when handler throws", async () => {
  const stub = makeStubFetch({
    routes: {
      [`GET /jobs/agent/${AGENT_ID}`]: {
        body: {
          jobs: [jobResponse({ job_id: "job_e", agent_id: AGENT_ID, status: "pending" })],
        },
      },
      "POST /jobs/job_e/claim": {
        body: { job_id: "job_e", status: "claimed", claim_token: "ct-e" },
      },
      "POST /jobs/job_e/fail": {
        body: jobResponse({ job_id: "job_e", status: "failed", error_message: "boom" }),
      },
    },
  });
  const client = makeClient(stub);
  const server = new AgentServer({
    apiKey: "az_worker",
    agentId: AGENT_ID,
    client,
    pollIntervalMs: 5,
    heartbeatIntervalMs: 10_000,
    handler: () => {
      throw new Error("boom");
    },
  });
  await server.runOnce();
  const fails = stub.callsFor("POST", "/jobs/job_e/fail");
  assert.equal(fails.length, 1);
  assert.deepEqual(fails[0]?.body, { error_message: "boom", claim_token: "ct-e" });
});

test("AgentServer posts clarification_request and re-runs handler with answer", async () => {
  let firstCall = true;
  const stub = makeStubFetch({
    routes: {
      [`GET /jobs/agent/${AGENT_ID}`]: {
        body: {
          jobs: [
            jobResponse({
              job_id: "job_clar",
              agent_id: AGENT_ID,
              status: "pending",
              input_payload: { city: "" },
            }),
          ],
        },
      },
      "POST /jobs/job_clar/claim": {
        body: { job_id: "job_clar", status: "claimed", claim_token: "ct-c" },
      },
      "POST /jobs/job_clar/messages": ({ body }) => ({
        body: { message_id: 99, type: (body as Record<string, unknown>).type, payload: (body as Record<string, unknown>).payload },
      }),
      "POST /jobs/job_clar/complete": ({ body }) => ({
        body: jobResponse({
          job_id: "job_clar",
          status: "complete",
          output_payload: (body as Record<string, unknown>).output_payload ?? null,
        }),
      }),
    },
  });
  const client = makeClient(stub);
  let observedCity: string | null = null;
  const server = new AgentServer({
    apiKey: "az_worker",
    agentId: AGENT_ID,
    client,
    pollIntervalMs: 5,
    heartbeatIntervalMs: 10_000,
    onClarificationNeeded: () => "Tokyo",
    handler: (input) => {
      if (firstCall) {
        firstCall = false;
        throw new ClarificationNeeded("Which city?");
      }
      observedCity = (input as { __clarification__?: string }).__clarification__ ?? null;
      return { city: observedCity };
    },
  });
  await server.runOnce();
  assert.equal(observedCity, "Tokyo");
  const posted = stub.callsFor("POST", "/jobs/job_clar/messages");
  assert.equal(posted.length, 1);
  assert.deepEqual(posted[0]?.body, {
    type: "clarification_request",
    payload: { question: "Which city?" },
  });
  const completes = stub.callsFor("POST", "/jobs/job_clar/complete");
  assert.equal(completes.length, 1);
  assert.deepEqual(completes[0]?.body, {
    output_payload: { city: "Tokyo" },
    claim_token: "ct-c",
  });
});

test("AgentServer.stop() exits the start() loop", async () => {
  const stub = makeStubFetch({
    routes: {
      [`GET /jobs/agent/${AGENT_ID}`]: { body: { jobs: [] } },
    },
  });
  const client = makeClient(stub);
  const server = new AgentServer({
    apiKey: "az_worker",
    agentId: AGENT_ID,
    client,
    pollIntervalMs: 5,
    heartbeatIntervalMs: 10_000,
    handler: () => ({}),
  });
  const running = server.start();
  await new Promise((r) => setTimeout(r, 20));
  server.stop();
  await running;
  assert.ok(stub.callsFor("GET", `/jobs/agent/${AGENT_ID}`).length >= 1);
});
