import { useEffect, useMemo } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { TableKit } from "@tiptap/extension-table";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function TiptapAnswer({ content }: { content: string }) {
  // Apply Markdown's HTML/link safety rules before importing any editor nodes.
  const html = useMemo(
    () =>
      renderToStaticMarkup(
        <Markdown
          skipHtml
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            ),
            img: ({ alt }) => <span>{alt}</span>,
          }}
        >
          {content}
        </Markdown>,
      ),
    [content],
  );
  const editor = useEditor({
    editable: false,
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        link: {
          openOnClick: true,
          autolink: false,
          HTMLAttributes: { target: "_blank", rel: "noreferrer noopener" },
        },
      }),
      TableKit,
    ],
    content: html,
    editorProps: {
      attributes: { "aria-label": "Repository response", role: "document" },
    },
  });
  useEffect(() => {
    // Replace the entire document: don't append the final answer to the work log.
    editor?.commands.setContent(html, { emitUpdate: false });
  }, [editor, html]);
  return (
    <EditorContent
      className="answer-copy answer-copy--tiptap"
      editor={editor}
    />
  );
}
