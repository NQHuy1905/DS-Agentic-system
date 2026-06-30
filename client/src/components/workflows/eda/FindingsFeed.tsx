import type { Finding } from "../../../types/eda";

interface Props {
  findings: Finding[];
  phaseOrder: string[];
}

const SEVERITY_STYLES: Record<Finding["severity"], string> = {
  info: "bg-gray-100 text-gray-600 border-gray-300",
  warn: "bg-amber-100 text-amber-700 border-amber-300",
  critical: "bg-red-100 text-red-700 border-red-300",
};

function SeverityBadge({ severity }: { severity: Finding["severity"] }) {
  return (
    <span
      className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${SEVERITY_STYLES[severity]}`}
    >
      {severity}
    </span>
  );
}

/**
 * Live findings list grouped by phase. Phases render in arrival order
 * (phaseOrder), each with its findings beneath.
 */
export function FindingsFeed({ findings, phaseOrder }: Props) {
  if (findings.length === 0 && phaseOrder.length === 0) {
    return (
      <p className="px-1 py-4 text-sm text-gray-400">
        Waiting for findings…
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {phaseOrder.map((phase) => {
        const rows = findings.filter((f) => f.phase === phase);
        return (
          <section key={phase}>
            <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-gray-400">
              {phase}
            </h4>
            {rows.length === 0 ? (
              <p className="pl-1 text-xs italic text-gray-300">running…</p>
            ) : (
              <ul className="space-y-1.5">
                {rows.map((f) => (
                  <li
                    key={f.id}
                    className="flex items-start gap-2 rounded border border-gray-200 bg-white p-2 text-sm"
                  >
                    <SeverityBadge severity={f.severity} />
                    <div className="min-w-0">
                      {f.column && (
                        <span className="mr-1 font-mono text-xs text-gray-500">
                          {f.column}
                        </span>
                      )}
                      <span className="text-gray-800">{f.description}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
