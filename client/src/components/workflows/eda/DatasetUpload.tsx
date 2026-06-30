import { useRef, useState } from "react";

interface Props {
  onFile: (file: File) => void;
  disabled?: boolean;
}

/** Drag-and-drop + click-to-pick CSV upload zone. */
export function DatasetUpload({ onFile, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    onFile(files[0]);
  }

  return (
    <div className="flex flex-col items-center justify-center h-full p-6">
      <div
        onClick={() => !disabled && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!disabled) handleFiles(e.dataTransfer.files);
        }}
        className={[
          "w-full max-w-md flex flex-col items-center justify-center gap-3",
          "rounded-xl border-2 border-dashed p-10 text-center transition-colors",
          disabled
            ? "cursor-not-allowed border-gray-200 text-gray-300"
            : "cursor-pointer text-gray-500 hover:border-blue-400 hover:text-blue-500",
          dragging ? "border-blue-500 bg-blue-50" : "border-gray-300",
        ].join(" ")}
      >
        <span className="text-4xl">📂</span>
        <p className="text-base font-medium">Drop a CSV file here</p>
        <p className="text-sm">or click to browse</p>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          disabled={disabled}
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>
    </div>
  );
}
