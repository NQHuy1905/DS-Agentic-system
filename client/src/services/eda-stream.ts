/**
 * SSE client for EDA run streams.
 *
 * Real stream: EventSource reconnects automatically; the browser sends
 * Last-Event-ID on reconnect and the backend replays the tail from the
 * durable per-run event buffer. We track the highest seen event id and
 * de-dupe replayed events so resumed-run findings + report_ready arrive
 * exactly once.
 *
 * Mock stream: scripted EDAEvent[] replay with realistic delays; pauses at
 * interrupt checkpoints until triggerMockResume() is called.
 */
import type { EDAEvent, Finding } from "../types/eda";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

export interface EDAStreamCallbacks {
  onEvent: (event: EDAEvent) => void;
  onError?: (error: Event) => void;
  onOpen?: () => void;
}

// ---------------------------------------------------------------------------
// Real EventSource stream
// ---------------------------------------------------------------------------

export function openEDAStream(
  runId: string,
  callbacks: EDAStreamCallbacks
): () => void {
  let lastSeenId = -1;
  let source: EventSource | null = null;
  let closed = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function connect() {
    if (closed) return;
    source = new EventSource(`${BASE_URL}/eda/stream/${runId}`);

    source.onopen = () => callbacks.onOpen?.();

    source.onmessage = (e: MessageEvent) => {
      try {
        const event = JSON.parse(e.data as string) as EDAEvent;
        if (event.id <= lastSeenId) return; // de-dupe replayed events on reconnect
        lastSeenId = event.id;
        callbacks.onEvent(event);
      } catch {
        // Malformed frame — skip silently
      }
    };

    source.onerror = (e: Event) => {
      callbacks.onError?.(e);
      source?.close();
      source = null;
      if (!closed) {
        reconnectTimer = setTimeout(connect, 2000);
      }
    };
  }

  connect();

  return () => {
    closed = true;
    if (reconnectTimer !== null) clearTimeout(reconnectTimer);
    source?.close();
    source = null;
  };
}

// ---------------------------------------------------------------------------
// Mock stream — dev/demo use only
// ---------------------------------------------------------------------------

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// Resume channel: resolved by triggerMockResume() after an interrupt
let _mockResumeResolve: (() => void) | null = null;

/** Unblocks the mock stream after an interrupt (called by mockResumeEda). */
export function triggerMockResume(): void {
  _mockResumeResolve?.();
  _mockResumeResolve = null;
}

function makeFinding(
  id: string,
  phase: string,
  severity: Finding["severity"],
  description: string,
  column: string | null = null
): Finding {
  return {
    id,
    phase,
    column,
    observed: null,
    expected: null,
    severity,
    description,
    evidence_ref: `obs-${id}`,
    root_cause: null,
    decision: null,
  };
}

const MOCK_PHASE1: EDAEvent[] = [
  { type: "phase_start", id: 1, phase: "framing" },
  {
    type: "interrupt",
    id: 2,
    checkpoint: "contract",
    payload: {
      objective:
        "Identify data quality issues and key statistical patterns in the uploaded dataset.",
      grain: "row",
      expectations: {
        expected_dtypes: [],
        ranges: [],
        null_priors: [],
        row_magnitude: null,
        valid_categories: [],
        notes: "Auto-generated framing from first-contact scan.",
      },
    },
  },
];

const MOCK_PHASE2: EDAEvent[] = [
  { type: "phase_start", id: 3, phase: "univariate" },
  {
    type: "finding",
    id: 4,
    finding: makeFinding(
      "f-01",
      "univariate",
      "info",
      "age: normal distribution (mean=34.2, std=12.1)",
      "age"
    ),
  },
  {
    type: "finding",
    id: 5,
    finding: makeFinding(
      "f-02",
      "univariate",
      "warn",
      "income: 8.3% missing values — above 5% threshold",
      "income"
    ),
  },
  {
    type: "finding",
    id: 6,
    finding: makeFinding(
      "f-03",
      "univariate",
      "critical",
      "customer_id: 412 duplicate values — possible key integrity violation",
      "customer_id"
    ),
  },
  { type: "phase_start", id: 7, phase: "bivariate" },
  {
    type: "finding",
    id: 8,
    finding: makeFinding(
      "f-04",
      "bivariate",
      "warn",
      "income vs purchase_amount: strong correlation (r=0.87) — multicollinearity risk",
      "income"
    ),
  },
  {
    type: "finding",
    id: 9,
    finding: makeFinding(
      "f-05",
      "bivariate",
      "info",
      "age vs churn_flag: no significant correlation found",
      "age"
    ),
  },
  {
    type: "interrupt",
    id: 10,
    checkpoint: "review",
    payload: {
      summary:
        "EDA complete. 5 findings across 2 phases. Review before generating the report.",
    },
  },
];

const MOCK_PHASE3: EDAEvent[] = [
  { type: "report_ready", id: 11, report_url: "/api/v1/eda/report/mock-run-001" },
];

/**
 * Scripted mock SSE stream. Emits events with realistic delays, pausing at
 * interrupt checkpoints until triggerMockResume() is called.
 */
export function openMockEDAStream(callbacks: EDAStreamCallbacks): () => void {
  let cancelled = false;

  async function run() {
    await delay(300);
    callbacks.onOpen?.();

    for (const batch of [MOCK_PHASE1, MOCK_PHASE2, MOCK_PHASE3]) {
      for (const event of batch) {
        if (cancelled) return;
        callbacks.onEvent(event);
        await delay(event.type === "finding" ? 700 : 300);
        if (event.type === "interrupt") {
          await new Promise<void>((resolve) => {
            _mockResumeResolve = resolve;
          });
          if (cancelled) return;
          await delay(200);
        }
      }
    }
  }

  run().catch(console.error);

  return () => {
    cancelled = true;
    // Unblock any pending interrupt so the async loop can exit cleanly
    _mockResumeResolve?.();
    _mockResumeResolve = null;
  };
}
