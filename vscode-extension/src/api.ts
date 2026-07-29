export const SUPPORTED_API_MAJOR = 1;

export interface JoeStatus {
  version: string;
  api_version: string;
  project: string;
  providers: string[];
  modes: string[];
}

export interface JoeConversation {
  id: string;
  title?: string;
  updated_at?: number;
  project_id?: string;
}

export interface JoeConversationDetail extends JoeConversation {
  messages?: Array<{ role: string; content: string }>;
  [key: string]: unknown;
}

export interface JoeActiveRun {
  run_id: string;
  conversation_id: string;
  request: string;
}

export interface JoeRunEvent {
  event_id: number;
  type: string;
  message?: string;
  response?: string;
  content?: string;
  text?: string;
  [key: string]: unknown;
}

export class JoeApiError extends Error {
  constructor(
    message: string,
    readonly status?: number
  ) {
    super(message);
  }
}
export class JoeConnectionError extends JoeApiError {}
export class JoeCompatibilityError extends JoeApiError {}

export class JoeClient {
  constructor(private readonly baseUrl: string) {}

  async status(): Promise<JoeStatus> {
    const status = await this.get<JoeStatus>("/api/status");
    const major = Number.parseInt(status.api_version?.split(".")[0] || "", 10);
    if (major !== SUPPORTED_API_MAJOR) {
      throw new JoeCompatibilityError(
        `API Joe incompatible : ${status.api_version || "non versionnée"}`
      );
    }
    return status;
  }

  async conversations(): Promise<JoeConversation[]> {
    return this.get<JoeConversation[]>("/api/conversations");
  }

  async conversation(id: string): Promise<JoeConversationDetail> {
    return this.get<JoeConversationDetail>(
      `/api/conversations/${encodeURIComponent(id)}`
    );
  }

  async createConversation(): Promise<JoeConversation> {
    return this.request<JoeConversation>("POST", "/api/conversations", {});
  }

  async startRun(conversationId: string, request: string): Promise<string> {
    const result = await this.request<{ run_id: string }>("POST", "/api/runs", {
      conversation_id: conversationId,
      request,
    });
    return result.run_id;
  }

  async cancelRun(runId: string): Promise<boolean> {
    const result = await this.request<{ cancelled: boolean }>(
      "POST",
      `/api/runs/${encodeURIComponent(runId)}/cancel`,
      {}
    );
    return result.cancelled;
  }

  async activeRuns(): Promise<JoeActiveRun[]> {
    return this.get<JoeActiveRun[]>("/api/runs/active");
  }

  async *events(
    runId: string,
    after = 0,
    signal?: AbortSignal
  ): AsyncGenerator<JoeRunEvent> {
    const path = `/api/events/${encodeURIComponent(runId)}?after=${after}`;
    const response = await this.fetch(path, { signal });
    await this.ensureOk(response, path);
    if (!response.body) {
      throw new JoeApiError("Joe a renvoyé un flux SSE vide.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = block
            .split("\n")
            .filter(line => line.startsWith("data:"))
            .map(line => line.slice(5).trimStart())
            .join("\n");
          if (data) {
            yield JSON.parse(data) as JoeRunEvent;
          }
          boundary = buffer.indexOf("\n\n");
        }
        if (done) {
          break;
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  private async get<T>(path: string): Promise<T> {
    const response = await this.fetch(path);
    await this.ensureOk(response, path);
    return response.json() as Promise<T>;
  }

  private async request<T>(
    method: string,
    path: string,
    payload: unknown
  ): Promise<T> {
    const response = await this.fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await this.ensureOk(response, path);
    return response.json() as Promise<T>;
  }

  private async fetch(path: string, init?: RequestInit): Promise<Response> {
    try {
      return await fetch(new URL(path, this.baseUrl), init);
    } catch (error) {
      throw new JoeConnectionError(
        `Serveur Joe inaccessible : ${error instanceof Error ? error.message : error}`
      );
    }
  }

  private async ensureOk(response: Response, path: string): Promise<void> {
    if (!response.ok) {
      let detail = "";
      try {
        const payload = (await response.json()) as { error?: unknown };
        detail = typeof payload.error === "string" ? payload.error : "";
      } catch {
        // Le code HTTP reste l'autorité quand le serveur renvoie autre chose.
      }
      throw new JoeApiError(
        detail || `Joe HTTP ${response.status} sur ${path}`,
        response.status
      );
    }
  }
}
