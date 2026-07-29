import { isValidElement, type ReactNode, useMemo, useState } from "react";

import type { EvidenceRange } from "@sentia/protocol";
import Markdown from "react-markdown";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import css from "react-syntax-highlighter/dist/esm/languages/prism/css";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import markup from "react-syntax-highlighter/dist/esm/languages/prism/markup";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import remarkGfm from "remark-gfm";

SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("css", css);
SyntaxHighlighter.registerLanguage("html", markup);
SyntaxHighlighter.registerLanguage("javascript", javascript);
SyntaxHighlighter.registerLanguage("js", javascript);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("jsx", jsx);
SyntaxHighlighter.registerLanguage("markup", markup);
SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("py", python);
SyntaxHighlighter.registerLanguage("tsx", tsx);
SyntaxHighlighter.registerLanguage("typescript", typescript);
SyntaxHighlighter.registerLanguage("ts", typescript);

interface MarkdownAnswerProps {
  content: string;
  evidence?: readonly EvidenceRange[];
  onOpenEvidence?: (evidence: EvidenceRange) => void;
}

interface MarkdownNode {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
}

interface CodeElementProps {
  children?: ReactNode;
  className?: string;
}

const EVIDENCE_LINK_PREFIX = "#sentia-evidence-";

export function MarkdownAnswer({
  content,
  evidence = [],
  onOpenEvidence,
}: MarkdownAnswerProps) {
  const evidencePlugin = useMemo(
    () => remarkEvidenceLinks(evidence),
    [evidence],
  );

  return (
    <div className="answer-copy">
      <Markdown
        components={{
          a: ({ children, href }) => {
            const evidenceIndex = evidenceIndexFromHref(href);
            const target =
              evidenceIndex === null ? undefined : evidence[evidenceIndex];
            if (target && onOpenEvidence) {
              return (
                <button
                  aria-label={`Open ${formatEvidenceReference(target)} in the editor`}
                  className="file-reference"
                  onClick={() => onOpenEvidence(target)}
                  type="button"
                >
                  <span aria-hidden="true" className="file-reference__icon">
                    ↗
                  </span>
                  {formatEvidenceReference(target)}
                </button>
              );
            }
            return (
              <a href={href} rel="noreferrer" target="_blank">
                {children}
              </a>
            );
          },
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        }}
        remarkPlugins={[remarkGfm, evidencePlugin]}
        skipHtml
      >
        {content}
      </Markdown>
    </div>
  );
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const element = isValidElement<CodeElementProps>(children) ? children : null;
  const code = textContent(element?.props.children ?? children).replace(
    /\n$/,
    "",
  );
  const language = languageFromClassName(element?.props.className);
  const displayLanguage = languageLabel(language);
  const showLineNumbers = code.includes("\n");

  async function copyCode(): Promise<void> {
    await navigator.clipboard?.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_500);
  }

  return (
    <div className="code-block">
      <div className="code-block__toolbar">
        <span>{displayLanguage}</span>
        <button onClick={() => void copyCode()} type="button">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <SyntaxHighlighter
        codeTagProps={{ className: "code-block__code" }}
        customStyle={{
          background: "#0d1117",
          margin: 0,
          padding: "12px 14px",
        }}
        language={language}
        lineNumberStyle={{
          color: "#56606b",
          minWidth: "2.4em",
          paddingRight: "1em",
          userSelect: "none",
        }}
        showLineNumbers={showLineNumbers}
        style={vscDarkPlus}
        wrapLongLines
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

function remarkEvidenceLinks(evidence: readonly EvidenceRange[]) {
  const paths = [...new Set(evidence.map((item) => item.path))].sort(
    (left, right) => right.length - left.length,
  );
  const matcher = paths.length
    ? new RegExp(
        `(${paths.map(escapeRegExp).join("|")}):(\\d+)(?:-(\\d+))?`,
        "g",
      )
    : null;

  return () =>
    (tree: MarkdownNode): void => {
      if (!matcher) {
        return;
      }
      transformTextNodes(tree, evidence, matcher);
    };
}

function transformTextNodes(
  node: MarkdownNode,
  evidence: readonly EvidenceRange[],
  matcher: RegExp,
): void {
  if (!node.children || ["code", "inlineCode", "link"].includes(node.type)) {
    return;
  }

  node.children = node.children.flatMap((child) => {
    if (child.type !== "text" || child.value === undefined) {
      transformTextNodes(child, evidence, matcher);
      return [child];
    }
    return splitEvidenceReferences(child.value, evidence, matcher);
  });
}

function splitEvidenceReferences(
  value: string,
  evidence: readonly EvidenceRange[],
  matcher: RegExp,
): MarkdownNode[] {
  const nodes: MarkdownNode[] = [];
  let cursor = 0;
  matcher.lastIndex = 0;

  for (const match of value.matchAll(matcher)) {
    const matchIndex = match.index;
    const startLine = Number(match[2]);
    const endLine = match[3] === undefined ? undefined : Number(match[3]);
    const evidenceIndex = evidence.findIndex(
      (item) =>
        item.path === match[1] &&
        item.startLine === startLine &&
        (endLine === undefined || item.endLine === endLine),
    );
    if (evidenceIndex < 0) {
      continue;
    }
    if (matchIndex > cursor) {
      nodes.push({ type: "text", value: value.slice(cursor, matchIndex) });
    }
    nodes.push({
      type: "link",
      url: `${EVIDENCE_LINK_PREFIX}${evidenceIndex}`,
      children: [{ type: "text", value: match[0] }],
    });
    cursor = matchIndex + match[0].length;
  }

  if (cursor === 0) {
    return [{ type: "text", value }];
  }
  if (cursor < value.length) {
    nodes.push({ type: "text", value: value.slice(cursor) });
  }
  return nodes;
}

function evidenceIndexFromHref(href: string | undefined): number | null {
  if (!href?.startsWith(EVIDENCE_LINK_PREFIX)) {
    return null;
  }
  const index = Number(href.slice(EVIDENCE_LINK_PREFIX.length));
  return Number.isInteger(index) && index >= 0 ? index : null;
}

function formatEvidenceReference(evidence: EvidenceRange): string {
  const lines =
    evidence.startLine === evidence.endLine
      ? `L${evidence.startLine}`
      : `L${evidence.startLine}–${evidence.endLine}`;
  return `${evidence.path}:${lines}`;
}

function languageFromClassName(className: string | undefined): string {
  const language = className?.match(/language-([\w-]+)/)?.[1]?.toLowerCase();
  return language && /^[\w-]+$/.test(language) ? language : "text";
}

function languageLabel(language: string): string {
  const labels: Record<string, string> = {
    bash: "Shell",
    css: "CSS",
    html: "HTML",
    javascript: "JavaScript",
    js: "JavaScript",
    json: "JSON",
    jsx: "JSX",
    markup: "HTML",
    py: "Python",
    python: "Python",
    text: "Code",
    ts: "TypeScript",
    tsx: "TSX",
    typescript: "TypeScript",
  };
  return labels[language] ?? language.toUpperCase();
}

function textContent(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(textContent).join("");
  }
  return "";
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
