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

export class JoeApiError extends Error {}

export class JoeClient {
  constructor(private readonly baseUrl: string) {}

  async status(): Promise<JoeStatus> {
    const status = await this.get<JoeStatus>("/api/status");
    const major = Number.parseInt(status.api_version?.split(".")[0] || "", 10);
    if (major !== SUPPORTED_API_MAJOR) {
      throw new JoeApiError(
        `API Joe incompatible : ${status.api_version || "non versionnée"}`
      );
    }
    return status;
  }

  async conversations(): Promise<JoeConversation[]> {
    return this.get<JoeConversation[]>("/api/conversations");
  }

  private async get<T>(path: string): Promise<T> {
    let response: Response;
    try {
      response = await fetch(new URL(path, this.baseUrl));
    } catch (error) {
      throw new JoeApiError(
        `Serveur Joe inaccessible : ${error instanceof Error ? error.message : error}`
      );
    }
    if (!response.ok) {
      throw new JoeApiError(`Joe HTTP ${response.status} sur ${path}`);
    }
    return response.json() as Promise<T>;
  }
}
