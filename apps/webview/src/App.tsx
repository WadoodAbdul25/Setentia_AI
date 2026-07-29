import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type { EditorBridge } from "@sentia/editor-client";
import type { AgentProvider, EvidenceRange } from "@sentia/protocol";
import clsx from "clsx";

import {
  resolveSentiaMode,
  StateJourney,
  type ModePreference,
} from "./StateJourney";
import { MarkdownAnswer } from "./MarkdownAnswer";
import { useSentiaStore } from "./store";

interface AppProps {
  bridge: EditorBridge;
}

export function App({ bridge }: AppProps) {
  const [modePreference, setModePreference] = useState<ModePreference>("auto");
  const [question, setQuestion] = useState("What is this codebase about?");
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null);
  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null);
  const submittedVoiceTurnsRef = useRef(new Set<string>());
  const {
    agent,
    anthropic,
    deepgram,
    events,
    extensionVersion,
    repositoryAnswer,
    requestError,
    sidecar,
    workspaceName,
    workspaceTrusted,
    voiceError,
    voiceStatus,
    voiceTranscript,
  } = useSentiaStore();
  const latestEvent = events.at(-1);
  const ready = sidecar.status === "healthy";
  const recommendedMode =
    repositoryAnswer?.payload.recommendedMode ?? "brainstorm";
  const mode = resolveSentiaMode(modePreference, recommendedMode);
  const answer =
    repositoryAnswer?.requestId === activeRequestId
      ? repositoryAnswer.payload
      : null;
  const error =
    requestError?.requestId === activeRequestId ? requestError.error : null;
  const asking = activeRequestId !== null && answer === null && error === null;
  const agentConnected = agent.connection?.connected === true;
  const providerName =
    agent.selectedProvider === "claude" ? "Claude Code" : "Codex";

  function selectAgent(provider: AgentProvider): void {
    bridge.post({
      type: "agent.select",
      requestId: crypto.randomUUID(),
      provider,
    });
  }

  function connectAgent(): void {
    bridge.post({ type: "agent.connect", requestId: crypto.randomUUID() });
  }

  function refreshAgent(): void {
    bridge.post({ type: "agent.refresh", requestId: crypto.randomUUID() });
  }

  function changeAgent(): void {
    bridge.post({
      type: "agent.clear_selection",
      requestId: crypto.randomUUID(),
    });
  }

  function connectAnthropic(): void {
    bridge.post({
      type: "anthropic.connect",
      requestId: crypto.randomUUID(),
    });
  }

  function connectDeepgram(): void {
    bridge.post({ type: "deepgram.connect", requestId: crypto.randomUUID() });
  }

  const submitQuestion = useCallback(
    (value: string, responseMode: "text" | "voice" = "text"): string | null => {
      const normalized = value.trim();
      if (!normalized) {
        return null;
      }
      const requestId = crypto.randomUUID();
      setActiveRequestId(requestId);
      bridge.post({
        type: "repository.ask",
        requestId,
        question: normalized,
        responseMode,
      });
      return requestId;
    },
    [bridge],
  );

  function askRepository(event: FormEvent): void {
    event.preventDefault();
    submitQuestion(question);
  }

  const stopVoiceCapture = useCallback(
    (cancel = false): void => {
      const sessionId = voiceSessionId;
      if (sessionId) {
        bridge.post({
          type: cancel ? "voice.cancel" : "voice.stop",
          requestId: crypto.randomUUID(),
          sessionId,
        });
      }
      setVoiceSessionId(null);
    },
    [bridge, voiceSessionId],
  );

  function startVoice(): void {
    if (!deepgram.connected) {
      connectDeepgram();
      return;
    }
    if (voiceSessionId) {
      stopVoiceCapture();
      return;
    }
    const sessionId = crypto.randomUUID();
    setVoiceSessionId(sessionId);
    bridge.post({
      type: "voice.start",
      requestId: crypto.randomUUID(),
      sessionId,
    });
  }

  useEffect(() => {
    if (!voiceTranscript || voiceTranscript.sessionId !== voiceSessionId) {
      return;
    }
    if (voiceTranscript.correctedTranscript) {
      setQuestion(voiceTranscript.correctedTranscript);
    }
    if (
      !voiceTranscript.isFinal ||
      !voiceTranscript.correctedTranscript.trim()
    ) {
      return;
    }
    const turnKey = `${voiceTranscript.sessionId}:${String(voiceTranscript.turnIndex)}`;
    if (submittedVoiceTurnsRef.current.has(turnKey)) {
      return;
    }
    submittedVoiceTurnsRef.current.add(turnKey);
    stopVoiceCapture();
    setVoiceSessionId(null);
    submitQuestion(voiceTranscript.correctedTranscript, "voice");
  }, [stopVoiceCapture, submitQuestion, voiceSessionId, voiceTranscript]);

  function openEvidence(evidence: EvidenceRange): void {
    bridge.post({
      type: "editor.open_evidence",
      requestId: crypto.randomUUID(),
      evidence,
    });
  }
  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand-mark" aria-hidden="true">
          S
        </div>
        <div>
          <h1>Sentia</h1>
          <p>Bring your codebase to life.</p>
        </div>
        <span
          className={clsx("status-dot", ready && "status-dot--ready")}
          title={sidecar.status}
        />
      </header>

      <section className="workspace-card" aria-live="polite">
        <div>
          <span className="eyebrow">Workspace</span>
          <strong>{workspaceName ?? "No folder open"}</strong>
        </div>
        <span
          className={clsx(
            "trust-badge",
            workspaceTrusted && "trust-badge--trusted",
          )}
        >
          {workspaceTrusted ? "Trusted" : "Restricted"}
        </span>
      </section>

      <section className="mode-switcher" aria-label="Sentia mode">
        <button
          aria-pressed={modePreference === "auto"}
          className={clsx(modePreference === "auto" && "is-active")}
          onClick={() => setModePreference("auto")}
        >
          Auto
        </button>
        <button
          aria-pressed={modePreference === "brainstorm"}
          className={clsx(modePreference === "brainstorm" && "is-active")}
          onClick={() => setModePreference("brainstorm")}
        >
          Brainstorm
        </button>
        <button
          aria-pressed={modePreference === "build"}
          className={clsx(modePreference === "build" && "is-active")}
          onClick={() => setModePreference("build")}
        >
          Plan &amp; Build
        </button>
      </section>

      <section className="mode-routing" aria-live="polite">
        <span className="eyebrow">
          {modePreference === "auto" ? "AI-selected mode" : "Manual override"}
        </span>
        <strong>{mode === "brainstorm" ? "Brainstorm" : "Plan & Build"}</strong>
        <p>
          {modePreference === "auto"
            ? (repositoryAnswer?.payload.modeReason ??
              "Sentia will classify your next request automatically.")
            : "Sentia will keep this mode until you return to Auto."}
        </p>
      </section>

      <StateJourney mode={mode} state={sidecar.workflowState} />

      {!ready ? (
        <section className="empty-state">
          <span className="pulse" aria-hidden="true" />
          <h2>Waking up the codebase…</h2>
          <p>
            {sidecar.message ?? "Sentia is starting its private local sidecar."}
          </p>
          {sidecar.status === "failed" ? (
            <button
              className="primary-button"
              onClick={() =>
                bridge.post({
                  type: "sidecar.restart",
                  requestId: crypto.randomUUID(),
                })
              }
            >
              Retry local sidecar
            </button>
          ) : null}
        </section>
      ) : agent.selectedProvider === null ? (
        <section className="connection-card provider-choice" aria-live="polite">
          <span className="eyebrow">Coding agent</span>
          <h2>Choose your coding agent</h2>
          <p>
            Sentia will remember this choice. You can change it later without
            changing the workspace.
          </p>
          <div className="provider-grid">
            <button
              className="provider-option"
              onClick={() => selectAgent("claude")}
              type="button"
            >
              <strong>Claude Code</strong>
              <span>Claude Agent SDK · Anthropic authentication</span>
            </button>
            <button
              className="provider-option"
              onClick={() => selectAgent("codex")}
              type="button"
            >
              <strong>Codex</strong>
              <span>OpenAI Codex SDK · ChatGPT or API login</span>
            </button>
          </div>
        </section>
      ) : !agentConnected ? (
        <section className="connection-card">
          <span className="eyebrow">Coding agent</span>
          <h2>Connect {providerName}</h2>
          <p>
            {agent.selectedProvider === "claude"
              ? "Connect an Anthropic Console API key. It stays in VS Code's encrypted secret storage."
              : "Sentia reuses your local Codex login. Connect opens OpenAI's browser authentication flow when needed."}
          </p>
          {agent.connection?.message ? (
            <p className="inline-error" role="alert">
              {agent.connection.message}
            </p>
          ) : null}
          <div className="connection-actions">
            <button className="primary-button" onClick={connectAgent}>
              Connect {providerName}
            </button>
            {agent.selectedProvider === "codex" ? (
              <button onClick={refreshAgent} type="button">
                Check connection
              </button>
            ) : null}
            <button onClick={changeAgent} type="button">
              Choose another agent
            </button>
          </div>
        </section>
      ) : agent.selectedProvider === "claude" && !anthropic.connected ? (
        <section className="connection-card">
          <span className="eyebrow">Repository intelligence</span>
          <h2>Connect Claude repository intelligence</h2>
          <p>
            Claude repository explanations use the Anthropic Messages API.
            Connect an API key before asking questions.
          </p>
          {anthropic.message ? (
            <p className="inline-error" role="alert">
              {anthropic.message}
            </p>
          ) : null}
          <div className="connection-actions">
            <button className="primary-button" onClick={connectAnthropic}>
              Connect repository intelligence
            </button>
            <button onClick={changeAgent} type="button">
              Choose another agent
            </button>
          </div>
        </section>
      ) : (
        <>
          <section className="connection-row">
            <span>
              <span className="connection-indicator" aria-hidden="true" />
              {providerName} connected
            </span>
            <button onClick={changeAgent}>Change agent</button>
          </section>

          <form className="question-card" onSubmit={askRepository}>
            <div className="question-card__heading">
              <label htmlFor="sentia-question">Ask the codebase</label>
              <button
                aria-label={
                  voiceSessionId ? "Stop listening" : "Ask with voice"
                }
                className={clsx(
                  "voice-button",
                  voiceSessionId && "is-listening",
                )}
                disabled={!workspaceTrusted || asking}
                onClick={() => void startVoice()}
                title={
                  deepgram.connected
                    ? "Ask with Deepgram Flux"
                    : "Connect Deepgram Flux"
                }
                type="button"
              >
                {voiceSessionId
                  ? "Stop"
                  : deepgram.connected
                    ? "Mic"
                    : "Connect voice"}
              </button>
            </div>
            <textarea
              id="sentia-question"
              maxLength={2000}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="What is this codebase about?"
              rows={3}
              value={question}
            />
            <button
              className="primary-button"
              disabled={asking || !workspaceTrusted}
              type="submit"
            >
              {asking ? "Reading codebase…" : "Ask Sentia"}
            </button>
            {voiceSessionId && voiceStatus?.sessionId === voiceSessionId ? (
              <p className="voice-status" aria-live="polite">
                {voiceStatus.message ?? "Listening…"}
              </p>
            ) : null}
            {voiceError && voiceError.sessionId === voiceSessionId ? (
              <p className="inline-error" role="alert">
                {voiceError.error}
              </p>
            ) : null}
            {voiceTranscript?.corrections.some(
              (correction) => correction.applied,
            ) ? (
              <p className="voice-correction">
                Repository spelling applied:{" "}
                {voiceTranscript.corrections
                  .filter((correction) => correction.applied)
                  .map((correction) => correction.replacement)
                  .join(", ")}
              </p>
            ) : null}
          </form>

          {error ? (
            <section className="answer-card answer-card--error" role="alert">
              <h2>Sentia needs attention</h2>
              <p>{error}</p>
              <button
                className="diagnostics-button"
                onClick={() =>
                  bridge.post({
                    type: "diagnostics.open",
                    requestId: crypto.randomUUID(),
                  })
                }
                type="button"
              >
                Open Sentia Diagnostics
              </button>
            </section>
          ) : null}

          {answer ? (
            <section className="answer-card" aria-live="polite">
              <div className="answer-card__heading">
                <h2>About this codebase</h2>
                <span>
                  {answer.filesRead} of {answer.filesScanned} files read
                </span>
              </div>
              <MarkdownAnswer
                content={answer.answer}
                evidence={answer.evidence}
                onOpenEvidence={openEvidence}
              />
              <div className="answer-usage" aria-label="Model token usage">
                <span>
                  {answer.usage.inputTokens.toLocaleString()} input tokens
                </span>
                <span>
                  {answer.usage.outputTokens.toLocaleString()} output tokens
                </span>
              </div>
              <details className="selection-report">
                <summary>Files Sentia selected</summary>
                <ul>
                  {answer.selectedFiles.map((path) => (
                    <li key={path}>{path}</li>
                  ))}
                </ul>
              </details>
              {answer.evidence.length > 0 ? (
                <div className="evidence-list">
                  <span className="eyebrow">Evidence</span>
                  {answer.evidence.map((evidence) => (
                    <button
                      key={`${evidence.path}:${evidence.startLine}:${evidence.endLine}`}
                      onClick={() => openEvidence(evidence)}
                    >
                      <strong>{evidence.label ?? evidence.path}</strong>
                      <span>
                        {evidence.path}:{evidence.startLine}
                      </span>
                    </button>
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}
        </>
      )}

      <footer className="runtime-footer">
        <span>Extension {extensionVersion ?? "—"}</span>
        <span>Sidecar {sidecar.version ?? "—"}</span>
        <span>{agent.selectedProvider ?? "no agent"}</span>
        <span>
          {answer?.model.replace("claude-", "") ??
            (agent.selectedProvider === "claude"
              ? anthropic.model.replace("claude-", "")
              : "account default")}
        </span>
        <span>{latestEvent?.type ?? sidecar.workflowState}</span>
      </footer>
    </main>
  );
}
