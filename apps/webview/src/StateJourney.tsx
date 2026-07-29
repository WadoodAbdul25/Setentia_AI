import type { WorkflowState, WorkingMode } from "@sentia/protocol";
import clsx from "clsx";

export type SentiaMode = WorkingMode;
export type ModePreference = "auto" | SentiaMode;

export function resolveSentiaMode(
  preference: ModePreference,
  recommendedMode: SentiaMode,
): SentiaMode {
  return preference === "auto" ? recommendedMode : preference;
}

const journeySteps = [
  "Brainstorm",
  "Plan mode",
  "Prompting",
  "Implementing",
  "Testing",
  "Review",
] as const;

const stateIndex: Partial<Record<WorkflowState, number>> = {
  discussing: 0,
  planning: 1,
  awaiting_approval: 1,
  prompting: 2,
  executing: 3,
  testing: 4,
  completed: 5,
};

export function getJourneyIndex(
  state: WorkflowState,
  mode: SentiaMode,
): number {
  if (state === "ready") {
    return mode === "brainstorm" ? 0 : 1;
  }
  return stateIndex[state] ?? -1;
}

export function getActivityLabel(
  state: WorkflowState,
  mode: SentiaMode,
): string {
  const labels: Record<WorkflowState, string> = {
    disconnected: "Disconnected",
    starting: "Starting Sentia",
    indexing: "Reading the codebase",
    ready: mode === "brainstorm" ? "Brainstorm" : "Plan mode",
    discussing: "Brainstorming",
    planning: "Creating a plan",
    awaiting_approval: "Waiting for approval",
    prompting: "Prompting coding agent",
    executing: "Implementing",
    testing: "Testing changes",
    completed: "Ready for review",
    failed: "Needs attention",
    cancelled: "Cancelled",
  };
  return labels[state];
}

interface StateJourneyProps {
  mode: SentiaMode;
  state: WorkflowState;
}

export function StateJourney({ mode, state }: StateJourneyProps) {
  const activeIndex = getJourneyIndex(state, mode);
  const activity = getActivityLabel(state, mode);

  return (
    <section
      className="state-journey"
      aria-label={`Current activity: ${activity}`}
    >
      <div className="state-journey__header">
        <span className="eyebrow">Current state</span>
        <strong>{activity}</strong>
      </div>
      <ol className="state-journey__track">
        {journeySteps.map((step, index) => (
          <li
            aria-current={index === activeIndex ? "step" : undefined}
            className={clsx(
              index < activeIndex && "is-complete",
              index === activeIndex && "is-current",
            )}
            key={step}
          >
            <span className="state-journey__dot" aria-hidden="true" />
            <span>{step}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
