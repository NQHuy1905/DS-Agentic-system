import type { LLMConfig, WorkflowId } from "../../types";
import { EDAWorkflow } from "../workflows/EDAWorkflow";
import { FeatureEngineeringWorkflow } from "../workflows/FeatureEngineeringWorkflow";
import { HyperparamOptWorkflow } from "../workflows/HyperparamOptWorkflow";
import { ModelMonitoringWorkflow } from "../workflows/ModelMonitoringWorkflow";
import { PipelineOrchestrationWorkflow } from "../workflows/PipelineOrchestrationWorkflow";

interface Props {
  activeWorkflow: WorkflowId;
  llmConfig: LLMConfig;
}

const WORKFLOW_MAP: Record<WorkflowId, React.ComponentType<{ llmConfig: LLMConfig }>> = {
  eda: EDAWorkflow,
  "feature-engineering": FeatureEngineeringWorkflow,
  "hyperparam-opt": HyperparamOptWorkflow,
  "model-monitoring": ModelMonitoringWorkflow,
  pipeline: PipelineOrchestrationWorkflow,
};

export function RightPanel({ activeWorkflow, llmConfig }: Props) {
  const ActiveWorkflow = WORKFLOW_MAP[activeWorkflow];

  return (
    <main className="flex-1 bg-gray-950 overflow-auto">
      <ActiveWorkflow llmConfig={llmConfig} />
    </main>
  );
}
