import { useState } from "react";

interface Props {
  value: string;
  onChange: (key: string) => void;
}

export function ApiKeyInput({ value, onChange }: Props) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-gray-400 uppercase tracking-wide">
        API Key
      </label>
      <div className="relative">
        <input
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="sk-..."
          className="w-full rounded-md border border-gray-600 bg-gray-800 px-3 py-2 pr-10 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-200 text-xs"
        >
          {visible ? "Hide" : "Show"}
        </button>
      </div>
    </div>
  );
}
