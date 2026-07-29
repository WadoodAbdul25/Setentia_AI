# Sentia State Machine

The sidecar owns the durable workflow state. The extension mirrors it and the
React webview renders it. A state change is valid only when accepted and emitted
by the sidecar.

## States

| State               | Meaning                                               | Mutating operations allowed? |
| ------------------- | ----------------------------------------------------- | ---------------------------- |
| `disconnected`      | Extension has no authenticated sidecar connection     | No                           |
| `starting`          | Extension is launching and verifying its sidecar      | No                           |
| `indexing`          | Sidecar is building or refreshing repository context  | No                           |
| `ready`             | Repository context is available and no turn is active | No                           |
| `discussing`        | A read-only answer or brainstorm is streaming         | No                           |
| `planning`          | Sentia is producing a proposed implementation plan    | No                           |
| `awaiting_approval` | A specific plan revision is waiting for the user      | No                           |
| `prompting`         | Approved context is being handed to the coding agent  | No                           |
| `executing`         | An approved coding run is using allowed tools         | Approved scope only          |
| `testing`           | Approved verification commands are running            | Approved checks only         |
| `completed`         | Run finished and results are available for review     | No                           |
| `failed`            | A turn or run ended with a surfaced error             | No                           |
| `cancelled`         | The user or supervisor stopped the active operation   | No                           |

## Valid transitions

```text
disconnected -> starting
starting -> indexing | ready | failed | disconnected
indexing -> ready | failed | cancelled | disconnected
ready -> discussing | planning | indexing | disconnected
discussing -> ready | failed | cancelled | disconnected
planning -> awaiting_approval | ready | failed | cancelled | disconnected
awaiting_approval -> planning | prompting | ready | cancelled | disconnected
prompting -> executing | failed | cancelled | disconnected
executing -> testing | completed | failed | cancelled | disconnected
testing -> completed | failed | cancelled | disconnected
completed -> ready | discussing | planning | indexing | disconnected
failed -> ready | starting | disconnected
cancelled -> ready | disconnected
```

## Enforcement rules

- The model may recommend the non-mutating working mode (`brainstorm` or
  `build`), and the user may override that recommendation.
- Model routing never directly enters `prompting`, `executing`, `testing`, or
  `completed`; those states require validated approval and observable runtime
  events.
- All transitions include a monotonically increasing event sequence.
- Approval includes the exact plan ID and revision it authorizes.
- `prompting` cannot be entered without a valid, unexpired approval.
- `executing` can be entered only from an approved `prompting` handoff.
- Cancellation is idempotent and must reach a terminal state.
- Extension reload reconnects and replays events after its last acknowledged
  sequence rather than inventing a new state.
- A sidecar version mismatch transitions to `failed`; the extension does not
  connect to an unknown protocol.
