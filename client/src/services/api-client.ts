import type { LLMConfig, WorkflowId } from "../types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

const WORKFLOW_ENDPOINTS: Record<WorkflowId, string> = {
  eda: "eda/run",
  "feature-engineering": "feature-engineering/run",
  "hyperparam-opt": "hyperparam-opt/run",
  "model-monitoring": "model-monitoring/run",
  pipeline: "pipeline/run",
};

export async function runWorkflow(
  workflowId: WorkflowId,
  llmConfig: LLMConfig,
  payload: Record<string, unknown>
) {
  const endpoint = WORKFLOW_ENDPOINTS[workflowId];
  const response = await fetch(`${BASE_URL}/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ llm_config: llmConfig, payload }),
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}
