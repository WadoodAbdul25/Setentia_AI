import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type { EditorBridge } from "@sentia/editor-client";
import type {
  AgentProvider,
  EvidenceRange,
  VoiceProvider,
} from "@sentia/protocol";
import clsx from "clsx";

import {
  resolveSentiaMode,
  StateJourney,
  type ModePreference,
} from "./StateJourney";
import { MarkdownAnswer } from "./MarkdownAnswer";
import { useSentiaStore } from "./store";
import {
  DEEPGRAM_AUTO_SUBMIT_DELAY_MS,
  resolveVoiceTurnAction,
} from "./voiceSubmission";

interface AppProps {
  bridge: EditorBridge;
}

interface PendingVoiceSubmission {
  transcript: string;
  turnKey: string;
}

export function App({ bridge }: AppProps) {
  const [modePreference, setModePreference] = useState<ModePreference>("auto");
  const [question, setQuestion] = useState("What is this codebase about?");
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null);
  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null);
  const [voiceSubmitRequested, setVoiceSubmitRequested] = useState(false);
  const [pendingVoiceSubmission, setPendingVoiceSubmission] =
    useState<PendingVoiceSubmission | null>(null);
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
    openaiVoice,
    voiceProvider,
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
  const voiceConnected =
    voiceProvider === "deepgram" ? deepgram.connected : openaiVoice.connected;
  const voiceProviderName =
    voiceProvider === "deepgram" ? "Deepgram Flux" : "OpenAI Voice";

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

  function connectOpenAIVoice(): void {
    bridge.post({
      type: "openai_voice.connect",
      requestId: crypto.randomUUID(),
    });
  }

  function selectVoiceProvider(provider: VoiceProvider): void {
    if (voiceSessionId) {
      cancelVoiceCapture();
    }
    bridge.post({
      type: "voice.provider.select",
      requestId: crypto.randomUUID(),
      provider,
    });
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
    if (voiceSessionId) {
      return;
    }
    submitQuestion(question);
  }

  const cancelVoiceCapture = useCallback((): void => {
    const sessionId = voiceSessionId;
    if (sessionId) {
      bridge.post({
        type: "voice.cancel",
        requestId: crypto.randomUUID(),
        sessionId,
      });
    }
    setPendingVoiceSubmission(null);
    setVoiceSubmitRequested(false);
    setVoiceSessionId(null);
  }, [bridge, voiceSessionId]);

  const finishVoiceCapture = useCallback((): void => {
    const sessionId = voiceSessionId;
    if (!sessionId || voiceSubmitRequested) {
      return;
    }
    setVoiceSubmitRequested(true);
    bridge.post({
      type: "voice.stop",
      requestId: crypto.randomUUID(),
      sessionId,
    });
  }, [bridge, voiceSessionId, voiceSubmitRequested]);

  function startVoice(): void {
    if (!voiceConnected) {
      if (voiceProvider === "deepgram") {
        connectDeepgram();
      } else {
        connectOpenAIVoice();
      }
      return;
    }
    const sessionId = crypto.randomUUID();
    setPendingVoiceSubmission(null);
    setVoiceSubmitRequested(false);
    setVoiceSessionId(sessionId);
    bridge.post({
      type: "voice.start",
      requestId: crypto.randomUUID(),
      sessionId,
    });
  }

  const submitVoiceTurn = useCallback(
    (turn: PendingVoiceSubmission): void => {
      if (submittedVoiceTurnsRef.current.has(turn.turnKey)) {
        return;
      }
      submittedVoiceTurnsRef.current.add(turn.turnKey);
      setPendingVoiceSubmission(null);
      setVoiceSubmitRequested(false);
      setVoiceSessionId(null);
      submitQuestion(turn.transcript, "voice");
    },
    [submitQuestion],
  );

  useEffect(() => {
    if (!voiceTranscript || voiceTranscript.sessionId !== voiceSessionId) {
      return;
    }
    if (voiceTranscript.correctedTranscript) {
      setQuestion(voiceTranscript.correctedTranscript);
    }
    const turnKey = `${voiceTranscript.sessionId}:${String(voiceTranscript.turnIndex)}`;
    if (submittedVoiceTurnsRef.current.has(turnKey)) {
      return;
    }
    const turn = {
      turnKey,
      transcript: voiceTranscript.correctedTranscript,
    };
    const action = resolveVoiceTurnAction({
      provider: voiceProvider,
      submitRequested: voiceSubmitRequested,
      isFinal: voiceTranscript.isFinal,
      transcript: voiceTranscript.correctedTranscript,
    });
    if (action === "submit") {
      submitVoiceTurn(turn);
      return;
    }
    if (action !== "queue" || pendingVoiceSubmission) {
      return;
    }
    setPendingVoiceSubmission(turn);
    bridge.post({
      type: "voice.stop",
      requestId: crypto.randomUUID(),
      sessionId: voiceTranscript.sessionId,
    });
  }, [
    bridge,
    pendingVoiceSubmission,
    submitVoiceTurn,
    voiceProvider,
    voiceSessionId,
    voiceSubmitRequested,
    voiceTranscript,
  ]);

  useEffect(() => {
    if (!pendingVoiceSubmission) {
      return;
    }
    const timer = window.setTimeout(() => {
      submitVoiceTurn(pendingVoiceSubmission);
    }, DEEPGRAM_AUTO_SUBMIT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [pendingVoiceSubmission, submitVoiceTurn]);

  useEffect(() => {
    if (
      voiceSubmitRequested &&
      voiceStatus?.sessionId === voiceSessionId &&
      voiceStatus.state === "closed"
    ) {
      const timer = window.setTimeout(() => {
        setVoiceSubmitRequested(false);
        setVoiceSessionId(null);
      }, 500);
      return () => window.clearTimeout(timer);
    }
  }, [voiceSessionId, voiceStatus, voiceSubmitRequested]);

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
            <div className="voice-provider-row">
              <span>Voice provider</span>
              <div
                aria-label="Voice provider"
                className="voice-provider-toggle"
              >
                <button
                  aria-pressed={voiceProvider === "deepgram"}
                  disabled={Boolean(voiceSessionId) || asking}
                  onClick={() => selectVoiceProvider("deepgram")}
                  type="button"
                >
                  Deepgram
                </button>
                <button
                  aria-pressed={voiceProvider === "openai"}
                  disabled={Boolean(voiceSessionId) || asking}
                  onClick={() => selectVoiceProvider("openai")}
                  type="button"
                >
                  OpenAI
                </button>
              </div>
            </div>
            {voiceProvider === "openai" ? (
              <p className="voice-disclosure">
                Spoken output uses an AI-generated OpenAI voice. An OpenAI API
                key with billing is required separately from ChatGPT or Codex.
              </p>
            ) : null}
            <div className="question-card__heading">
              <label htmlFor="sentia-question">Ask the codebase</label>
              <div className="voice-actions">
                <button
                  aria-label={
                    pendingVoiceSubmission
                      ? "Send speech now"
                      : voiceSessionId
                        ? "Stop and submit speech"
                        : "Ask with voice"
                  }
                  className={clsx(
                    "voice-button",
                    voiceSessionId && "is-listening",
                  )}
                  disabled={!workspaceTrusted || asking || voiceSubmitRequested}
                  onClick={() =>
                    pendingVoiceSubmission
                      ? submitVoiceTurn(pendingVoiceSubmission)
                      : voiceSessionId
                        ? finishVoiceCapture()
                        : void startVoice()
                  }
                  title={
                    pendingVoiceSubmission
                      ? "Submit the detected Deepgram turn now"
                      : voiceSessionId
                        ? "Stop listening and ask Sentia"
                        : voiceConnected
                          ? `Ask with ${voiceProviderName}`
                          : `Connect ${voiceProviderName}`
                  }
                  type="button"
                >
                  {voiceSessionId
                    ? pendingVoiceSubmission
                      ? "Send now"
                      : voiceSubmitRequested
                        ? "Finishing…"
                        : "Stop"
                    : voiceConnected
                      ? "Mic"
                      : `Connect ${voiceProvider === "deepgram" ? "Deepgram" : "OpenAI"}`}
                </button>
                {voiceSessionId ? (
                  <button
                    aria-label="Cancel and discard speech"
                    className="voice-cancel-button"
                    onClick={cancelVoiceCapture}
                    title="Discard this recording without asking Sentia"
                    type="button"
                  >
                    Cancel
                  </button>
                ) : null}
              </div>
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
              disabled={asking || !workspaceTrusted || Boolean(voiceSessionId)}
              type="submit"
            >
              {asking ? "Reading codebase…" : "Ask Sentia"}
            </button>
            {pendingVoiceSubmission ? (
              <p className="voice-status" aria-live="polite">
                End of turn detected. Sending in two seconds—Cancel to discard.
              </p>
            ) : voiceSessionId && voiceStatus?.sessionId === voiceSessionId ? (
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
