import { lazy, Suspense } from "react";

import type { EvidenceRange, RepositoryActivity } from "@sentia/protocol";

import { MarkdownAnswer } from "./MarkdownAnswer";

// Keep the optional editor out of the default Markdown view's startup bundle.
const TiptapAnswer = lazy(() => import("./TiptapAnswer"));

export type AnswerView = "markdown" | "tiptap";

export function activityMarkdown(
  activities: readonly RepositoryActivity[],
  fallback: string,
  stopped = false,
): string {
  if (!activities.length) return escapeMarkdown(fallback);
  return activities
    .map((entry) => {
      const status =
        entry.status === "completed"
          ? "Done"
          : stopped
            ? "Stopped"
            : "In progress";
      return `- **${escapeMarkdown(entry.message)}** — ${status}${entry.details
        .map((detail) => `\n  - ${inlineCode(detail)}`)
        .join("")}`;
    })
    .join("\n\n");
}

function escapeMarkdown(text: string): string {
  // File names and English descriptions are data, not model-authored Markdown.
  return text
    .replace(/[\\`*_{}[\]()<>#+.!|~-]/g, "\\$&")
    .replace(/[\r\n]+/g, " ");
}

function inlineCode(text: string): string {
  const cleaned = text.replace(/[\r\n]+/g, " ");
  const fence = "`".repeat(
    Math.max(0, ...(cleaned.match(/`+/g) ?? []).map((match) => match.length)) +
      1,
  );
  return `${fence} ${cleaned} ${fence}`;
}

export function AnswerViewToggle({
  view,
  onChange,
}: {
  view: AnswerView;
  onChange: (view: AnswerView) => void;
}) {
  return (
    <div className="answer-view-toggle" role="group" aria-label="Answer view">
      <button
        type="button"
        aria-pressed={view === "markdown"}
        onClick={() => onChange("markdown")}
      >
        Markdown
      </button>
      <button
        type="button"
        aria-pressed={view === "tiptap"}
        onClick={() => onChange("tiptap")}
      >
        Tiptap
      </button>
    </div>
  );
}

export function RepositoryAnswerBody({
  content,
  view,
  evidence = [],
  onOpenEvidence,
}: {
  content: string;
  view: AnswerView;
  evidence?: readonly EvidenceRange[];
  onOpenEvidence?: (evidence: EvidenceRange) => void;
}) {
  return view === "tiptap" ? (
    <Suspense fallback={<MarkdownAnswer content={content} />}>
      <TiptapAnswer content={content} />
    </Suspense>
  ) : (
    <MarkdownAnswer
      content={content}
      evidence={evidence}
      onOpenEvidence={onOpenEvidence}
    />
  );
}
