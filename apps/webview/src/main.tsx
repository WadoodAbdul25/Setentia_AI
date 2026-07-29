import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createEditorBridge } from "@sentia/editor-client";
import { extensionToWebviewMessageSchema } from "@sentia/protocol";

import { App } from "./App";
import { useSentiaStore } from "./store";
import "./styles.css";

const bridge = createEditorBridge();
bridge.subscribe((raw) => {
  const result = extensionToWebviewMessageSchema.safeParse(raw);
  if (!result.success) {
    return;
  }
  const message = result.data;
  if (message.type === "app.bootstrap") {
    useSentiaStore.getState().bootstrap(message.payload);
  } else if (message.type === "sidecar.status") {
    useSentiaStore.getState().setSidecar(message.payload);
  } else if (message.type === "anthropic.status") {
    useSentiaStore.getState().setAnthropic(message.payload);
  } else if (message.type === "deepgram.status") {
    useSentiaStore.getState().setDeepgram(message.payload);
  } else if (message.type === "agent.status") {
    useSentiaStore.getState().setAgent(message.payload);
  } else if (message.type === "sidecar.event") {
    useSentiaStore.getState().addEvent(message.payload);
  } else if (message.type === "repository.answer") {
    useSentiaStore
      .getState()
      .setRepositoryAnswer(message.requestId, message.payload);
  } else if (message.type === "request.error") {
    useSentiaStore.getState().setRequestError(message.requestId, message.error);
  } else if (message.type === "voice.status") {
    useSentiaStore
      .getState()
      .setVoiceStatus(message.sessionId, message.state, message.message);
  } else if (message.type === "voice.transcript") {
    useSentiaStore.getState().setVoiceTranscript(message.payload);
  } else if (message.type === "voice.error") {
    useSentiaStore.getState().setVoiceError(message.sessionId, message.error);
  }
});
bridge.post({ type: "ui.ready" });

const root = document.getElementById("root");
if (!root) {
  throw new Error("Sentia webview root is missing");
}

createRoot(root).render(
  <StrictMode>
    <App bridge={bridge} />
  </StrictMode>,
);
