import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  extensionToFlowMapPanelMessageSchema,
  type ExtensionToFlowMapPanelMessage,
  type FlowMapPanelToExtensionMessage,
} from "@sentia/protocol";
import "@xyflow/react/dist/style.css";

import { FlowMapApp } from "./FlowMapApp";
import "./flowMapStyles.css";

interface VsCodeApi {
  postMessage(message: FlowMapPanelToExtensionMessage): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

const vscode = acquireVsCodeApi();

function FlowMapRoot() {
  const [message, setMessage] = useState<ExtensionToFlowMapPanelMessage | null>(
    null,
  );

  useEffect(() => {
    const receive = (event: MessageEvent<unknown>): void => {
      const parsed = extensionToFlowMapPanelMessageSchema.safeParse(event.data);
      if (parsed.success) {
        setMessage(parsed.data);
      }
    };
    window.addEventListener("message", receive);
    vscode.postMessage({ type: "flow_map.ready" });
    return () => window.removeEventListener("message", receive);
  }, []);

  return (
    <FlowMapApp message={message} post={(value) => vscode.postMessage(value)} />
  );
}

const root = document.getElementById("root");
if (!root) {
  throw new Error("Sentia Flow Map root is missing");
}

createRoot(root).render(
  <StrictMode>
    <FlowMapRoot />
  </StrictMode>,
);
