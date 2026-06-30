/**
 * REST calls for the EDA workflow: upload a dataset, start a run, resume a run
 * after a human checkpoint. SSE streaming lives in eda-stream.ts.
 */
import type { LLMConfig } from "../types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

export interface UploadResult {
  dataset_ref: string;
}

export interface RunResult {
  run_id: string;
}

type Checkpoint = "contract" | "review";

async function asJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

/** Multipart upload of a dataset file; returns the server-side dataset ref. */
export async function uploadDataset(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${BASE_URL}/eda/upload`, {
    method: "POST",
    body: form,
  });
  return asJson<UploadResult>(response);
}

/** Kick off an EDA run against an uploaded dataset; returns the run id. */
export async function runEda(
  llmConfig: LLMConfig,
  datasetRef: string,
  objective: string
): Promise<RunResult> {
  const response = await fetch(`${BASE_URL}/eda/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      llm_config: llmConfig,
      dataset_ref: datasetRef,
      objective,
    }),
  });
  return asJson<RunResult>(response);
}

/** Resume a paused run after a human checkpoint (contract confirm / review). */
export async function resumeEda(
  runId: string,
  checkpoint: Checkpoint,
  response: Record<string, unknown>
): Promise<void> {
  const res = await fetch(`${BASE_URL}/eda/resume/${runId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ checkpoint, response }),
  });
  if (!res.ok) {
    throw new Error(`Resume failed: ${res.status} ${res.statusText}`);
  }
}
