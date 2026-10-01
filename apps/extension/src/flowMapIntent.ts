const FLOW_MAP_MARKER = String.raw`\bflow(?:[\s-]*map)\b`;
const NEGATED_FLOW_MAP = new RegExp(
  String.raw`\b(?:do\s+not|don't|dont|not|without)\b.{0,24}${FLOW_MAP_MARKER}`,
  "i",
);
const REQUESTED_FLOW_MAP = new RegExp(
  String.raw`\b(?:build|create|draw|generate|give|make|need|open|show|want)\b.{0,100}${FLOW_MAP_MARKER}`,
  "i",
);
const LEADING_FLOW_MAP = new RegExp(
  String.raw`^\s*(?:please\s+)?(?:a\s+)?${FLOW_MAP_MARKER}(?:\s|$|[:\-])`,
  "i",
);

export function explicitFlowMapQuestion(question: string): string | null {
  const normalized = question.trim();
  if (!normalized || NEGATED_FLOW_MAP.test(normalized)) {
    return null;
  }
  return REQUESTED_FLOW_MAP.test(normalized) ||
    LEADING_FLOW_MAP.test(normalized)
    ? normalized
    : null;
}
