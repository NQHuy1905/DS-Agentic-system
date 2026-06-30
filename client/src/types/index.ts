export type Provider = "openai" | "anthropic" | "google";

export type WorkflowId =
  | "eda"
  | "feature-engineering"
  | "hyperparam-opt"
  | "model-monitoring"
  | "pipeline";

export interface WorkflowTab {
  id: WorkflowId;
  label: string;
}

export interface LLMConfig {
  provider: Provider;
  model: string;
  apiKey: string;
}

export const WORKFLOWS: WorkflowTab[] = [
  { id: "eda", label: "Automated EDA" },
  { id: "feature-engineering", label: "Feature Engineering" },
  { id: "hyperparam-opt", label: "Hyperparameter Opt." },
  { id: "model-monitoring", label: "Model Monitoring" },
  { id: "pipeline", label: "Pipeline Orchestration" },
];

export const PROVIDER_MODELS: Record<Provider, string[]> = {
  openai: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
  anthropic: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
  google: ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash"],
};
