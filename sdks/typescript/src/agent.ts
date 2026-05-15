import {
  AgentmarketApiError,
  AgentmarketClient,
  AzteaClient,
  type ClientOptions,
  type JobMessageResponse,
  type JobResponse,
} from "./client";

type JsonValue = string | number | boolean | null | { [key: string]: JsonValue } | JsonValue[];
type JsonObject = { [key: string]: JsonValue };

const DEFAULT_POLL_INTERVAL_MS = 2000;
const DEFAULT_HEARTBEAT_INTERVAL_MS = 20_000;
const DEFAULT_LEASE_SECONDS = 300;
const DEFAULT_PENDING_BATCH = 10;
const DEFAULT_CLARIFICATION_TIMEOUT_SECONDS = 600;

/**
 * Thrown from inside a handler to ask the caller a question and resume with their answer.
 * Mirrors `ClarificationNeeded` in the Python SDK.
 */
export class ClarificationNeeded extends Error {
  constructor(public readonly question: string) {
    super(question);
    this.name = "ClarificationNeeded";
  }
}

export interface HandlerContext {
  readonly jobId: string;
  readonly claimToken: string;
  postProgress(payload: JsonObject): Promise<void>;
  emitPartial(payload: JsonObject): Promise<void>;
}

export type AgentHandler = (
  input: JsonObject,
  context: HandlerContext,
) => Promise<JsonObject> | JsonObject;

export interface AgentServerOptions {
  apiKey: string;
  agentId: string;
  handler: AgentHandler;
  baseUrl?: string;
  /** Existing client to reuse (otherwise one is constructed from `apiKey` + `baseUrl`). */
  client?: AgentmarketClient;
  leaseSeconds?: number;
  pollIntervalMs?: number;
  heartbeatIntervalMs?: number;
  /** Max pending jobs fetched per poll cycle. Each is processed sequentially. */
  pendingBatchSize?: number;
  /** Optional fetcher for clarification answers; if omitted, jobs that need clarification fail. */
  onClarificationNeeded?: (
    question: string,
    context: HandlerContext,
  ) => Promise<string | null | undefined> | string | null | undefined;
  /** Seconds to wait for caller's clarification_response before failing the job. */
  clarificationTimeoutSeconds?: number;
  /** Hook for logging / metrics. Never thrown by the server. */
  onError?: (error: unknown, jobId?: string) => void;
}

export class AgentServer {
  public readonly agentId: string;
  public readonly client: AgentmarketClient;
  private readonly handler: AgentHandler;
  private readonly leaseSeconds: number;
  private readonly pollIntervalMs: number;
  private readonly heartbeatIntervalMs: number;
  private readonly pendingBatchSize: number;
  private readonly clarificationTimeoutSeconds: number;
  private readonly onClarificationNeeded?: AgentServerOptions["onClarificationNeeded"];
  private readonly onError?: AgentServerOptions["onError"];

  private stopRequested = false;
  private loopPromise: Promise<void> | null = null;

  constructor(options: AgentServerOptions) {
    if (!options.apiKey && !options.client) {
      throw new Error("AgentServer requires apiKey or a pre-built client.");
    }
    if (!options.agentId) {
      throw new Error("AgentServer requires agentId.");
    }
    if (typeof options.handler !== "function") {
      throw new Error("AgentServer requires a handler function.");
    }

    this.agentId = options.agentId;
    this.client =
      options.client ??
      new AzteaClient({
        baseUrl: options.baseUrl,
        apiKey: options.apiKey,
      } as ClientOptions);
    this.handler = options.handler;
    this.leaseSeconds = options.leaseSeconds ?? DEFAULT_LEASE_SECONDS;
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    this.heartbeatIntervalMs = options.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS;
    this.pendingBatchSize = options.pendingBatchSize ?? DEFAULT_PENDING_BATCH;
    this.clarificationTimeoutSeconds =
      options.clarificationTimeoutSeconds ?? DEFAULT_CLARIFICATION_TIMEOUT_SECONDS;
    this.onClarificationNeeded = options.onClarificationNeeded;
    this.onError = options.onError;
  }

  /** Start the poll loop. Resolves when `stop()` is called and the loop exits. */
  start(): Promise<void> {
    if (this.loopPromise) return this.loopPromise;
    this.stopRequested = false;
    this.loopPromise = this.runLoop().finally(() => {
      this.loopPromise = null;
    });
    return this.loopPromise;
  }

  /** Request a graceful shutdown. Idempotent. */
  stop(): void {
    this.stopRequested = true;
  }

  /** Run a single poll-and-process cycle; returns the number of jobs processed. */
  async runOnce(): Promise<number> {
    let pending: JobResponse[];
    try {
      pending = await this.fetchPending();
    } catch (error) {
      this.reportError(error);
      return 0;
    }
    for (const job of pending) {
      if (this.stopRequested) break;
      await this.processJob(job);
    }
    return pending.length;
  }

  private async runLoop(): Promise<void> {
    while (!this.stopRequested) {
      const processed = await this.runOnce();
      if (this.stopRequested) break;
      if (processed === 0) {
        await sleep(this.pollIntervalMs);
      }
    }
  }

  private async fetchPending(): Promise<JobResponse[]> {
    const path = `/jobs/agent/${encodeURIComponent(this.agentId)}`;
    const response = await this.client.request<{ jobs?: unknown }>(path, {
      query: { status: "pending", limit: this.pendingBatchSize },
    });
    const jobs = (response as Record<string, unknown>).jobs;
    if (!Array.isArray(jobs)) return [];
    return jobs.filter((job): job is JobResponse => isJobResponseLike(job));
  }

  private async processJob(rawJob: JobResponse): Promise<void> {
    const jobId = rawJob.job_id;
    if (!jobId) return;

    let claimed: JobResponse;
    try {
      claimed = await this.client.jobs.claim(jobId, this.leaseSeconds);
    } catch (error) {
      // 409 = already claimed by someone else; treat all claim errors as skip.
      this.reportError(error, jobId);
      return;
    }
    const claimToken = (claimed as { claim_token?: unknown }).claim_token;
    if (typeof claimToken !== "string" || !claimToken) {
      this.reportError(new Error("claim returned no claim_token"), jobId);
      return;
    }

    const stopHeartbeat = this.startHeartbeat(jobId, claimToken);
    const context: HandlerContext = {
      jobId,
      claimToken,
      postProgress: (payload) => this.postMessage(jobId, "progress", payload),
      emitPartial: (payload) => this.postMessage(jobId, "partial_output", { payload }),
    };

    try {
      const input = coerceObject(rawJob.input_payload);
      const output = await this.runHandlerWithClarification(input, context);
      await this.client.jobs.complete(jobId, output, claimToken);
    } catch (error) {
      this.reportError(error, jobId);
      const message =
        error instanceof Error && error.message ? error.message : "Handler raised an error.";
      try {
        await this.client.jobs.fail(jobId, message, claimToken);
      } catch (failError) {
        this.reportError(failError, jobId);
      }
    } finally {
      stopHeartbeat();
    }
  }

  private async runHandlerWithClarification(
    input: JsonObject,
    context: HandlerContext,
  ): Promise<JsonObject> {
    try {
      return await this.handler(input, context);
    } catch (error) {
      if (!(error instanceof ClarificationNeeded)) throw error;
      await this.client.jobs.postMessage(context.jobId, "clarification_request", {
        question: error.question,
      });
      const answer = await this.awaitClarification(context, error.question);
      if (answer === null) {
        throw new Error("Timed out waiting for caller clarification.");
      }
      const retried = { ...input, __clarification__: answer } as JsonObject;
      return await this.handler(retried, context);
    }
  }

  private async awaitClarification(context: HandlerContext, question: string): Promise<string | null> {
    if (this.onClarificationNeeded) {
      const direct = await this.onClarificationNeeded(question, context);
      if (typeof direct === "string" && direct.length > 0) return direct;
    }
    const deadline = Date.now() + this.clarificationTimeoutSeconds * 1000;
    const seen = new Set<number>();
    while (Date.now() < deadline) {
      if (this.stopRequested) return null;
      let messages: JobMessageResponse[] = [];
      try {
        const response = await this.client.jobs.listMessages(context.jobId);
        const raw = (response as Record<string, unknown>).messages;
        if (Array.isArray(raw)) {
          messages = raw.filter((m): m is JobMessageResponse => !!m && typeof m === "object");
        }
      } catch (error) {
        this.reportError(error, context.jobId);
      }
      for (let i = messages.length - 1; i >= 0; i--) {
        const message = messages[i] as unknown as Record<string, unknown>;
        const id = message.message_id;
        if (typeof id === "number") {
          if (seen.has(id)) continue;
          seen.add(id);
        }
        if (message.type !== "clarification_response") continue;
        const payload = message.payload;
        if (payload && typeof payload === "object" && !Array.isArray(payload)) {
          const answer = (payload as Record<string, unknown>).answer;
          if (typeof answer === "string" && answer.length > 0) return answer;
        }
      }
      await sleep(this.pollIntervalMs);
    }
    return null;
  }

  private startHeartbeat(jobId: string, claimToken: string): () => void {
    let stopped = false;
    const tick = async () => {
      while (!stopped) {
        await sleep(this.heartbeatIntervalMs);
        if (stopped) return;
        try {
          await this.client.jobs.heartbeat(jobId, claimToken, this.leaseSeconds);
        } catch (error) {
          this.reportError(error, jobId);
          // Heartbeat failures end the loop; the handler will still resolve or fail
          // and the caller's lease eventually expires.
          return;
        }
      }
    };
    void tick();
    return () => {
      stopped = true;
    };
  }

  private async postMessage(jobId: string, type: string, payload: JsonObject): Promise<void> {
    try {
      await this.client.jobs.postMessage(jobId, type, payload);
    } catch (error) {
      this.reportError(error, jobId);
    }
  }

  private reportError(error: unknown, jobId?: string): void {
    if (!this.onError) return;
    try {
      this.onError(error, jobId);
    } catch {
      // Never throw from the error reporter itself.
    }
  }
}

function isJobResponseLike(value: unknown): value is JobResponse {
  return (
    !!value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof (value as { job_id?: unknown }).job_id === "string"
  );
}

function coerceObject(value: unknown): JsonObject {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as JsonObject;
  }
  return {};
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)));
}

export { AgentmarketApiError };
