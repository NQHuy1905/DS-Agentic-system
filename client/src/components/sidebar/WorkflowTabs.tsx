import type { WorkflowId } from "../../types";
import { WORKFLOWS } from "../../types";

interface Props {
  active: WorkflowId;
  onChange: (id: WorkflowId) => void;
}

export function WorkflowTabs({ active, onChange }: Props) {
  return (
    <nav className="flex flex-col gap-1">
      <span className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-1">
        Workflows
      </span>
      {WORKFLOWS.map((wf, idx) => (
        <button
          key={wf.id}
          onClick={() => onChange(wf.id)}
          className={[
            "flex items-center gap-2 rounded-md px-3 py-2 text-sm text-left transition-colors",
            active === wf.id
              ? "bg-blue-600 text-white"
              : "text-gray-300 hover:bg-gray-700",
          ].join(" ")}
        >
          <span className="w-5 h-5 flex items-center justify-center rounded-full bg-gray-700 text-xs font-bold shrink-0">
            {idx + 1}
          </span>
          {wf.label}
        </button>
      ))}
    </nav>
  );
}
