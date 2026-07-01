---
phase: 5
title: "Frontend EDA Panel"
status: completed
priority: P1
effort: "2-3d"
dependencies: [1]
---

# Phase 5: Frontend EDA Panel

## Overview
Full build of the EDA right-panel: dataset upload, run trigger, live SSE findings feed with severity styling, human-checkpoint prompts (contract confirm + final review), and report download. Builds against the Phase 1 event contract; validatable with a mock event stream before the backend exists.

## Requirements
- Functional: upload CSV → confirm framing contract → watch findings stream → respond to review interrupt → download report.
- Non-functional: resilient SSE (reconnect), severity color coding, no blocking UI during long runs.

## Architecture
New dir `client/src/components/workflows/eda/`, replacing the placeholder `EDAWorkflow.tsx` body:
- `EDAWorkflow.tsx` — orchestrates the panel state machine (idle → uploading → framing → running → interrupted → done).
- `DatasetUpload.tsx` — drag/drop + file picker → `POST /eda/upload`.
- `ContractConfirm.tsx` — renders Framing output (objective/grain/expectations), user edits + confirms → resume checkpoint `contract`.
- `FindingsFeed.tsx` — live list; severity badges (info/warn/critical); groups by phase.
- `ReviewPrompt.tsx` — final human review interrupt → resume checkpoint `review`.
- `ReportDownload.tsx` — fetch report URL.
- `client/src/services/eda-api.ts` — upload/run/resume fetch calls.
- `client/src/services/eda-stream.ts` — SSE client (`EventSource`) typed to `EDAEvent`. **Red-team fix:** the POST/run + GET/stream + POST/resume split means post-resume events + reconnect-gap events are lost without replay. `EventSource` sends `Last-Event-ID` automatically on reconnect; the backend (Phase 9) replays from the durable per-run buffer (Phase 1 event `id`). Client tracks last-seen `id`, de-dupes on reconnect so resumed-run findings + `report_ready` arrive reliably.
- `client/src/types/eda.ts` — TS mirror of Phase 1 event/finding schema.

## Related Code Files
- Create: `client/src/components/workflows/eda/*.tsx`, `client/src/services/eda-api.ts`, `client/src/services/eda-stream.ts`, `client/src/types/eda.ts`
- Modify: `client/src/components/workflows/EDAWorkflow.tsx` (replace placeholder body; keep export name — RightPanel imports it)

## Implementation Steps
1. `types/eda.ts`: mirror Finding + EDAEvent union from Phase 1.
2. `eda-stream.ts`: EventSource wrapper, parse `data:` lines, dispatch typed events, reconnect on drop.
3. `eda-api.ts`: upload (multipart), run, resume.
4. Build components; wire panel state machine in `EDAWorkflow.tsx`.
5. Severity styling via Tailwind (info=gray, warn=amber, critical=red).
6. Mock validation: a local mock emitter replaying a scripted `EDAEvent[]` to verify rendering + interrupt flow without backend.

## Success Criteria
- [ ] Upload → ref returned → run starts (against mock or real backend).
- [ ] Findings render live with correct severity styling, grouped by phase.
- [ ] Contract-confirm + review interrupts surface and resume correctly.
- [ ] Report download works.
- [ ] `npm run build` clean (0 TS errors).

## Risk Assessment
Risk: SSE contract drift vs backend. Mitigation: single source of truth = Phase 1 schema, mirrored in `types/eda.ts`; mock stream uses same types. Risk: interrupt UX (blocking vs inline) — open question from design doc. Mitigation: start inline (non-blocking panel prompt); revisit after demo.
