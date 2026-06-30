/**
 * TypeScript mirror of server/app/models/eda_schemas.py and eda_events.py.
 * Discriminated on the `type` field; every EDAEvent carries a monotonic `id`
 * so the stream endpoint can honor Last-Event-ID on reconnect.
 */

// ---------------------------------------------------------------------------
// Expectation model (framing output)
// ---------------------------------------------------------------------------

export interface ColumnDtype {
  column: string;
  expected_dtype: string;
}

export interface ColumnRange {
  column: string;
  min: number;
  max: number;
}

export interface ColumnNullPrior {
  column: string;
  expected_null_rate: number;
}

export interface ColumnCategories {
  column: string;
  valid_values: string[];
}

export interface ExpectationModel {
  expected_dtypes: ColumnDtype[];
  ranges: ColumnRange[];
  null_priors: ColumnNullPrior[];
  row_magnitude: number | null;
  valid_categories: ColumnCategories[];
  notes: string;
}

// ---------------------------------------------------------------------------
// Finding — evidence_ref is required (no default) per schema contract
// ---------------------------------------------------------------------------

export interface Finding {
  id: string;
  phase: string;
  column: string | null;
  observed: unknown;
  expected: unknown;
  severity: "info" | "warn" | "critical";
  description: string;
  evidence_ref: string;
  root_cause: string | null;
  decision: string | null;
}

// ---------------------------------------------------------------------------
// EDAEvent discriminated union (type is the discriminator, id is sequence)
// ---------------------------------------------------------------------------

export interface PhaseStartEvent {
  type: "phase_start";
  id: number;
  phase: string;
}

export interface FindingEvent {
  type: "finding";
  id: number;
  finding: Finding;
}

export interface InterruptEvent {
  type: "interrupt";
  id: number;
  checkpoint: "contract" | "review";
  payload: Record<string, unknown>;
}

export interface ReportReadyEvent {
  type: "report_ready";
  id: number;
  report_url: string;
}

export interface ErrorEvent {
  type: "error";
  id: number;
  message: string;
}

export type EDAEvent =
  | PhaseStartEvent
  | FindingEvent
  | InterruptEvent
  | ReportReadyEvent
  | ErrorEvent;

// Typed payload shape for the contract interrupt checkpoint
export interface ContractPayload {
  objective: string;
  grain: string;
  expectations: ExpectationModel;
}
