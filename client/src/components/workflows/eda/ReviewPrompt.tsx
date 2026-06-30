import { useState } from "react";

interface Props {
  summary: string;
  onResume: (response: Record<string, unknown>) => void;
  disabled?: boolean;
}

/**
 * Final human-review checkpoint. User can add notes and approve to generate the
 * report, which resumes the run at the `review` checkpoint.
 */
export function ReviewPrompt({ summary, onResume, disabled }: Props) {
  const [notes, setNotes] = useState("");

  return (
    <div className="rounded-lg border border-blue-300 bg-blue-50 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-lg">🔍</span>
        <h3 className="font-semibold text-blue-900">Final Review</h3>
      </div>

      <p className="text-sm text-gray-700">{summary}</p>

      <label className="block text-sm">
        <span className="text-gray-700 font-medium">Reviewer notes (optional)</span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          disabled={disabled}
          rows={2}
          className="mt-1 w-full rounded border border-gray-300 p-2 text-sm
            focus:border-blue-400 focus:outline-none disabled:bg-gray-100"
        />
      </label>

      <button
        onClick={() => onResume({ approved: true, notes })}
        disabled={disabled}
        className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium
          text-white hover:bg-blue-700 disabled:opacity-50"
      >
        Approve & Generate Report
      </button>
    </div>
  );
}
