import { useState } from "react";
import type { LLMConfig, WorkflowId, Provider } from "./types";
import { PROVIDER_MODELS } from "./types";
import { LeftPanel } from "./components/layout/LeftPanel";
import { RightPanel } from "./components/layout/RightPanel";

const DEFAULT_PROVIDER: Provider = "openai";

const DEFAULT_LLM_CONFIG: LLMConfig = {
  provider: DEFAULT_PROVIDER,
  model: PROVIDER_MODELS[DEFAULT_PROVIDER][0],
  apiKey: "",
};

export default function App() {
  const [llmConfig, setLLMConfig] = useState<LLMConfig>(DEFAULT_LLM_CONFIG);
  const [activeWorkflow, setActiveWorkflow] = useState<WorkflowId>("eda");

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-gray-950 text-gray-100">
      <LeftPanel
        llmConfig={llmConfig}
        activeWorkflow={activeWorkflow}
        onLLMConfigChange={setLLMConfig}
        onWorkflowChange={setActiveWorkflow}
      />
      <RightPanel activeWorkflow={activeWorkflow} llmConfig={llmConfig} />
    </div>
  );
}
