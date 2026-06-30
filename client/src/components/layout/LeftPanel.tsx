import type { LLMConfig, Provider, WorkflowId } from "../../types";
import { PROVIDER_MODELS } from "../../types";
import { ProviderDropdown } from "../sidebar/ProviderDropdown";
import { ModelDropdown } from "../sidebar/ModelDropdown";
import { ApiKeyInput } from "../sidebar/ApiKeyInput";
import { WorkflowTabs } from "../sidebar/WorkflowTabs";

interface Props {
  llmConfig: LLMConfig;
  activeWorkflow: WorkflowId;
  onLLMConfigChange: (config: LLMConfig) => void;
  onWorkflowChange: (id: WorkflowId) => void;
}

export function LeftPanel({
  llmConfig,
  activeWorkflow,
  onLLMConfigChange,
  onWorkflowChange,
}: Props) {
  const handleProviderChange = (provider: Provider) => {
    onLLMConfigChange({
      ...llmConfig,
      provider,
      model: PROVIDER_MODELS[provider][0],
    });
  };

  return (
    <aside className="w-64 shrink-0 flex flex-col gap-4 bg-gray-900 border-r border-gray-700 p-4 h-full overflow-y-auto">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-blue-400 text-lg font-bold">⚡</span>
        <span className="text-white font-semibold text-sm">DS Agent Assist</span>
      </div>

      <ProviderDropdown value={llmConfig.provider} onChange={handleProviderChange} />
      <ModelDropdown
        provider={llmConfig.provider}
        value={llmConfig.model}
        onChange={(model) => onLLMConfigChange({ ...llmConfig, model })}
      />
      <ApiKeyInput
        value={llmConfig.apiKey}
        onChange={(apiKey) => onLLMConfigChange({ ...llmConfig, apiKey })}
      />

      <hr className="border-gray-700" />

      <WorkflowTabs active={activeWorkflow} onChange={onWorkflowChange} />
    </aside>
  );
}
