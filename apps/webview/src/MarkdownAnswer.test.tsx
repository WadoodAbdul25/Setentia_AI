// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MarkdownAnswer } from "./MarkdownAnswer";

describe("MarkdownAnswer", () => {
  it("renders structured model output instead of raw markdown markers", () => {
    render(
      <MarkdownAnswer
        content={
          "## Architecture\n\n- **Frontend:** React\n- `Backend`: Django"
        }
      />,
    );

    expect(
      screen.getByRole("heading", { level: 2, name: "Architecture" }).tagName,
    ).toBe("H2");
    expect(screen.getByText("Frontend:").tagName).toBe("STRONG");
    expect(screen.getByText("Backend").tagName).toBe("CODE");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("does not render raw HTML supplied by a model", () => {
    const { container } = render(
      <MarkdownAnswer content={'Safe text <script>alert("no")</script>'} />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("Safe text", { exact: false })).toBeTruthy();
  });

  it("opens validated file references at their evidence range", () => {
    const onOpenEvidence = vi.fn();
    const evidence = {
      path: "src/services/repository.py",
      startLine: 18,
      endLine: 24,
      label: "Repository scanner",
    };

    render(
      <MarkdownAnswer
        content="The scanner starts here: src/services/repository.py:18-24."
        evidence={[evidence]}
        onOpenEvidence={onOpenEvidence}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Open src/services/repository.py:L18–24 in the editor",
      }),
    );
    expect(onOpenEvidence).toHaveBeenCalledOnce();
    expect(onOpenEvidence).toHaveBeenCalledWith(evidence);
  });

  it("leaves unvalidated file references as plain text", () => {
    render(
      <MarkdownAnswer
        content="This unsupported reference stays plain: src/unknown.py:1-4."
        evidence={[]}
        onOpenEvidence={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /src\/unknown\.py/ }),
    ).toBeNull();
    expect(screen.getByText(/src\/unknown\.py:1-4/)).toBeTruthy();
  });

  it("renders fenced source excerpts with a VS Code-style code frame", () => {
    const { container } = render(
      <MarkdownAnswer
        content={
          'src/app.py:10-12\n\n```python\ndef greet(name: str) -> str:\n    return f"Hello {name}"\n```'
        }
        evidence={[{ path: "src/app.py", startLine: 10, endLine: 12 }]}
      />,
    );

    const block = container.querySelector(".code-block");
    expect(block).not.toBeNull();
    expect(block?.textContent).toContain("Python");
    expect(block?.textContent).toContain("def greet(name: str) -> str:");
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
  });
});
