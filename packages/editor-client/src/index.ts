import type {
  ExtensionToWebviewMessage,
  WebviewToExtensionMessage,
} from "@sentia/protocol";

interface VsCodeApi<State = unknown> {
  getState(): State | undefined;
  postMessage(message: WebviewToExtensionMessage): void;
  setState(state: State): void;
}

declare function acquireVsCodeApi<State = unknown>(): VsCodeApi<State>;

export interface EditorBridge {
  post(message: WebviewToExtensionMessage): void;
  subscribe(listener: (message: ExtensionToWebviewMessage) => void): () => void;
}

export function createEditorBridge(): EditorBridge {
  const api = acquireVsCodeApi();

  return {
    post(message) {
      api.postMessage(message);
    },
    subscribe(listener) {
      const handleMessage = (event: MessageEvent<unknown>): void => {
        listener(event.data as ExtensionToWebviewMessage);
      };
      window.addEventListener("message", handleMessage);
      return () => window.removeEventListener("message", handleMessage);
    },
  };
}
