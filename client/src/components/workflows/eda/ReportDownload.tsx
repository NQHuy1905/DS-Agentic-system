interface Props {
  reportUrl: string;
  onReset: () => void;
}

const BASE_ORIGIN = (
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1"
).replace(/\/api\/v1\/?$/, "");

/**
 * Terminal panel state: the report is ready. Resolves a relative report_url
 * against the API origin and offers download + a reset to run another dataset.
 */
export function ReportDownload({ reportUrl, onReset }: Props) {
  const href = reportUrl.startsWith("http")
    ? reportUrl
    : `${BASE_ORIGIN}${reportUrl}`;

  return (
    <div className="rounded-lg border border-green-300 bg-green-50 p-4 space-y-3 text-center">
      <span className="text-3xl">✅</span>
      <h3 className="font-semibold text-green-900">Report Ready</h3>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="block w-full rounded bg-green-600 px-4 py-2 text-sm font-medium
          text-white hover:bg-green-700"
      >
        Download Report
      </a>
      <button
        onClick={onReset}
        className="text-sm text-gray-500 hover:text-gray-700 underline"
      >
        Analyze another dataset
      </button>
    </div>
  );
}
