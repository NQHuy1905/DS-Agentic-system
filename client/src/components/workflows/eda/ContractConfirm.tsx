import { useState } from "react";
import type { ContractPayload } from "../../../types/eda";

interface Props {
  payload: ContractPayload;
  onConfirm: (response: Record<string, unknown>) => void;
  disabled?: boolean;
}

/**
 * Renders the Framing contract (objective / grain / expectations) and lets the
 * user edit the objective + grain before confirming. Confirm resumes the run
 * at the `contract` checkpoint with the (possibly edited) values.
 */
export function ContractConfirm({ payload, onConfirm, disabled }: Props) {
  const [objective, setObjective] = useState(payload.objective);
  const [grain, setGrain] = useState(payload.grain);
  const exp = payload.expectations;

  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-lg">📝</span>
        <h3 className="font-semibold text-amber-900">Confirm Analysis Contract</h3>
      </div>

      <label className="block text-sm">
        <span className="text-gray-700 font-medium">Objective</span>
        <textarea
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          disabled={disabled}
          rows={3}
          className="mt-1 w-full rounded border border-gray-300 p-2 text-sm
            focus:border-blue-400 focus:outline-none disabled:bg-gray-100"
        />
      </label>

      <label className="block text-sm">
        <span className="text-gray-700 font-medium">Grain</span>
        <input
          value={grain}
          onChange={(e) => setGrain(e.target.value)}
          disabled={disabled}
          className="mt-1 w-full rounded border border-gray-300 p-2 text-sm
            focus:border-blue-400 focus:outline-none disabled:bg-gray-100"
        />
      </label>

      <div className="text-sm">
        <span className="text-gray-700 font-medium">Expectations</span>
        <ul className="mt-1 space-y-0.5 text-gray-600">
          <li>{exp.expected_dtypes.length} typed columns</li>
          <li>{exp.ranges.length} range constraints</li>
          <li>{exp.null_priors.length} null-rate priors</li>
          <li>{exp.valid_categories.length} categorical domains</li>
          <li>
            row magnitude:{" "}
            {exp.row_magnitude === null ? "unknown" : exp.row_magnitude}
          </li>
        </ul>
        {exp.notes && (
          <p className="mt-1 italic text-gray-500">{exp.notes}</p>
        )}
      </div>

      <button
        onClick={() => onConfirm({ objective, grain })}
        disabled={disabled}
        className="w-full rounded bg-amber-600 px-4 py-2 text-sm font-medium
          text-white hover:bg-amber-700 disabled:opacity-50"
      >
        Confirm & Continue
      </button>
    </div>
  );
}
