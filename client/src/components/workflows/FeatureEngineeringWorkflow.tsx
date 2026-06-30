import type { LLMConfig } from "../../types";

interface Props {
  llmConfig: LLMConfig;
}

export function FeatureEngineeringWorkflow({ llmConfig: _llmConfig }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-gray-500">
      <span className="text-4xl mb-3">🔧</span>
      <p className="text-lg font-medium">Agentic Feature Engineering and Selection</p>
      <p className="text-sm mt-1">Workflow coming soon</p>
    </div>
  );
}
