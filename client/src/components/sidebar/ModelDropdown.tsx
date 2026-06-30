import type { Provider } from "../../types";
import { PROVIDER_MODELS } from "../../types";

interface Props {
  provider: Provider;
  value: string;
  onChange: (model: string) => void;
}

export function ModelDropdown({ provider, value, onChange }: Props) {
  const models = PROVIDER_MODELS[provider];

  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-gray-400 uppercase tracking-wide">
        Model
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-gray-600 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
}
