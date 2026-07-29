import {
  JoeClient,
  JoeConnectionError,
  JoeStatus,
} from "./api";

export type RestartPlan =
  | { mode: "ready"; project: string }
  | { mode: "force"; project: string }
  | { mode: "blocked"; message: string }
  | { mode: "error"; message: string };

interface StatusReader {
  status(): Promise<JoeStatus>;
}

export async function planRestart(
  client: JoeClient,
  fallbackWorkspace: string | undefined
): Promise<RestartPlan> {
  let project: string;
  try {
    project = (await client.status()).project;
  } catch (error) {
    if (error instanceof JoeConnectionError && fallbackWorkspace) {
      return { mode: "force", project: fallbackWorkspace };
    }
    return {
      mode: "error",
      message: error instanceof Error ? error.message : String(error),
    };
  }

  try {
    const runs = await client.activeRuns();
    if (runs.length) {
      return {
        mode: "blocked",
        message: `${runs.length} tâche(s) encore active(s) : redémarrage refusé.`,
      };
    }
  } catch (error) {
    if (error instanceof JoeConnectionError) {
      return { mode: "force", project };
    }
    return {
      mode: "error",
      message: error instanceof Error ? error.message : String(error),
    };
  }
  return { mode: "ready", project };
}

export async function waitForJoe(
  client: StatusReader,
  attempts = 20,
  delayMs = 500
): Promise<JoeStatus> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await client.status();
    } catch (error) {
      if (!(error instanceof JoeConnectionError)) {
        throw error;
      }
      lastError = error;
      if (attempt + 1 < attempts) {
        await new Promise(resolve => setTimeout(resolve, delayMs));
      }
    }
  }
  throw lastError;
}
