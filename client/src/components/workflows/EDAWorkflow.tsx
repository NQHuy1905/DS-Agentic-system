import { useCallback, useRef, useState } from "react";
import type { LLMConfig } from "../../types";
import type {
  ContractPayload,
  EDAEvent,
  Finding,
} from "../../types/eda";
import { runEda, resumeEda, uploadDataset } from "../../services/eda-api";
import {
  openEDAStream,
  openMockEDAStream,
  triggerMockResume,
  type EDAStreamCallbacks,
} from "../../services/eda-stream";
import { DatasetUpload } from "./eda/DatasetUpload";
import { ContractConfirm } from "./eda/ContractConfirm";
import { FindingsFeed } from "./eda/FindingsFeed";
import { ReviewPrompt } from "./eda/ReviewPrompt";
import { ReportDownload } from "./eda/ReportDownload";

interface Props {
  llmConfig: LLMConfig;
}

type Status = "idle" | "uploading" | "running" | "interrupted" | "done" | "error";

interface InterruptState {
  checkpoint: "contract" | "review";
  payload: Record<string, unknown>;
}

const DEFAULT_OBJECTIVE = "Explore the dataset for quality issues and patterns.";

export function EDAWorkflow({ llmConfig }: Props) {
  const [status, setStatus] = useState<Status>("idle");
  const [findings, setFindings] = useState<Finding[]>([]);
  const [phaseOrder, setPhaseOrder] = useState<string[]>([]);
  const [interrupt, setInterrupt] = useState<InterruptState | null>(null);
  const [reportUrl, setReportUrl] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [useMock, setUseMock] = useState(false);

  const closeStream = useRef<(() => void) | null>(null);
  const runId = useRef<string | null>(null);

  const handleEvent = useCallback((event: EDAEvent) => {
    switch (event.type) {
      case "phase_start":
        setPhaseOrder((prev) =>
          prev.includes(event.phase) ? prev : [...prev, event.phase]
        );
        break;
      case "finding":
        setFindings((prev) => [...prev, event.finding]);
        break;
      case "interrupt":
        setInterrupt({ checkpoint: event.checkpoint, payload: event.payload });
        setStatus("interrupted");
        break;
      case "report_ready":
        setReportUrl(event.report_url);
        setStatus("done");
        break;
      case "error":
        setErrorMsg(event.message);
        setStatus("error");
        break;
    }
  }, []);

  const startStream = useCallback(
    (id: string | null) => {
      const callbacks: EDAStreamCallbacks = {
        onEvent: handleEvent,
        onError: () => {
          /* transient — stream auto-reconnects */
        },
      };
      closeStream.current = useMock
        ? openMockEDAStream(callbacks)
        : openEDAStream(id as string, callbacks);
    },
    [handleEvent, useMock]
  );

  const handleFile = useCallback(
    async (file: File) => {
      setStatus("uploading");
      setErrorMsg(null);
      try {
        if (useMock) {
          runId.current = "mock-run-001";
        } else {
          const { dataset_ref } = await uploadDataset(file);
          const { run_id } = await runEda(llmConfig, dataset_ref, DEFAULT_OBJECTIVE);
          runId.current = run_id;
        }
        setStatus("running");
        startStream(runId.current);
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : "Upload failed");
        setStatus("error");
      }
    },
    [llmConfig, startStream, useMock]
  );

  const handleResume = useCallback(
    async (response: Record<string, unknown>) => {
      if (!interrupt) return;
      const checkpoint = interrupt.checkpoint;
      setInterrupt(null);
      setStatus("running");
      try {
        if (useMock) {
          triggerMockResume();
        } else if (runId.current) {
          await resumeEda(runId.current, checkpoint, response);
        }
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : "Resume failed");
        setStatus("error");
      }
    },
    [interrupt, useMock]
  );

  const reset = useCallback(() => {
    closeStream.current?.();
    closeStream.current = null;
    runId.current = null;
    setFindings([]);
    setPhaseOrder([]);
    setInterrupt(null);
    setReportUrl(null);
    setErrorMsg(null);
    setStatus("idle");
  }, []);

  if (status === "idle") {
    return (
      <div className="flex h-full flex-col">
        <DatasetUpload onFile={handleFile} />
        <label className="flex items-center justify-center gap-2 pb-4 text-xs text-gray-400">
          <input
            type="checkbox"
            checked={useMock}
            onChange={(e) => setUseMock(e.target.checked)}
          />
          Use mock stream (no backend)
        </label>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      {status === "uploading" && (
        <p className="text-sm text-gray-500">Uploading & starting run…</p>
      )}

      {status === "error" && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          <p className="font-medium">Run failed</p>
          <p>{errorMsg}</p>
          <button onClick={reset} className="mt-2 underline">
            Start over
          </button>
        </div>
      )}

      {interrupt?.checkpoint === "contract" && (
        <ContractConfirm
          payload={interrupt.payload as unknown as ContractPayload}
          onConfirm={handleResume}
        />
      )}

      {interrupt?.checkpoint === "review" && (
        <ReviewPrompt
          summary={String(interrupt.payload.summary ?? "Review the findings.")}
          onResume={handleResume}
        />
      )}

      {status === "done" && reportUrl && (
        <ReportDownload reportUrl={reportUrl} onReset={reset} />
      )}

      {(status === "running" || status === "interrupted" || status === "done") && (
        <FindingsFeed findings={findings} phaseOrder={phaseOrder} />
      )}
    </div>
  );
}
