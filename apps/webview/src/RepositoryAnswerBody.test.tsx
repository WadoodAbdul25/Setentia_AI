// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { activityMarkdown, RepositoryAnswerBody } from "./RepositoryAnswerBody";

afterEach(cleanup);

describe("repository work log", () => {
  const content = activityMarkdown(
    [
      {
        id: "file_selection",
        stage: "file_selection",
        status: "completed",
        message: "Selected 8 files to inspect.",
        details: ["backend/auth/models.py"],
      },
      {
        id: "content_read",
        stage: "content_read",
        status: "completed",
        message: "Read evidence from 8 files.",
        details: ["5 files supplied as shortened excerpts."],
      },
    ],
    "Checking.",
  );

  it.each(["markdown", "tiptap"] as const)(
    "renders English milestones in %s and replaces them with the final answer",
    async (view) => {
      const { rerender, container } = render(
        <RepositoryAnswerBody view={view} content={content} />,
      );
      await waitFor(() =>
        expect(screen.getByText("Selected 8 files to inspect.")).toBeTruthy(),
      );
      expect(screen.getByText("backend/auth/models.py")).toBeTruthy();
      expect(
        screen.getByText("5 files supplied as shortened excerpts."),
      ).toBeTruthy();
      if (view === "tiptap")
        await waitFor(() =>
          expect(
            container.querySelector("[contenteditable='false']"),
          ).not.toBeNull(),
        );
      rerender(
        <RepositoryAnswerBody
          view={view}
          content={"## Final answer\n\nAuthentication uses tokens."}
        />,
      );
      await waitFor(() =>
        expect(
          screen.getByRole("heading", { name: "Final answer" }),
        ).toBeTruthy(),
      );
      expect(screen.getByText("Authentication uses tokens.")).toBeTruthy();
      expect(screen.queryByText("Selected 8 files to inspect.")).toBeNull();
      expect(screen.queryByText("backend/auth/models.py")).toBeNull();
    },
  );

  it("keeps Markdown-shaped filenames as literal data", () => {
    const malicious = activityMarkdown(
      [
        {
          id: "selection",
          stage: "file_selection",
          status: "completed",
          message: "Selected one file.",
          details: [
            "[click](https://example.com)",
            "<img src=x onerror=alert(1)>",
          ],
        },
      ],
      "Checking.",
    );
    const { container } = render(
      <RepositoryAnswerBody view="markdown" content={malicious} />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("[click](https://example.com)")).toBeTruthy();
  });

  it("does not import raw model HTML or unsafe links into Tiptap", async () => {
    const { container } = render(
      <RepositoryAnswerBody
        view="tiptap"
        content={
          'Safe text <script>alert("no")</script>\n\n[bad](javascript:alert%281%29)'
        }
      />,
    );
    await waitFor(() => expect(screen.getByText(/Safe text/)).toBeTruthy());
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[href^='javascript:']")).toBeNull();
  });

  it("preserves answer tables and fenced code in Tiptap", async () => {
    const { container } = render(
      <RepositoryAnswerBody
        view="tiptap"
        content={
          "| Feature | Status |\n| --- | --- |\n| Login | Present |\n\n```python\ndef login():\n    return True\n```"
        }
      />,
    );
    await waitFor(() =>
      expect(container.querySelector("table")).not.toBeNull(),
    );
    expect(screen.getByText("Login")).toBeTruthy();
    expect(container.querySelector("pre code")?.textContent).toContain(
      "def login():",
    );
  });
});
