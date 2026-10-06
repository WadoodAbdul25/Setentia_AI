# LiveKit + Cerebras voice architecture

Status: architecture and phased implementation for `Cerebras-Sentia`, October 4, 2026. The OpenAI LiveKit pipeline described below is implemented; continuous
microphone and intelligent interruption remain proposed.

## Implemented first phase

Written Q&A now streams through `/api/v1/repository/questions/stream`, and the
LiveKit socket forwards request-scoped `voice.answer.delta` and
`voice.answer.progress` events. SDK streams expose only the structured `answer`
field, never reasoning or raw JSON metadata. The UI marks it as a draft until the
final answer replaces it. Complete Markdown passages with valid source ranges
may enter Cerebras narration while repository generation continues; uncited or
unfinished passages are held until full-answer validation. This range check is
not independent semantic fact verification.

One speech worker serializes short stage phrases and answer passages, waiting
for native playback acknowledgments between segments. Progress phrases are fixed
application text (not model-invented status), capped at two with a 12-second
cooldown, and tied to actual work stages. They can be disabled with
`SENTIA_VOICE_PROGRESS_PHRASES=false`. A speech failure does not stop the written
answer stream. The old 60-second audio cap and overall 90-second speech deadline
are removed; provider connection timeouts remain in force.

The selected scope is OpenAI STT + Cerebras narration + OpenAI TTS, orchestrated
by one local LiveKit `AgentSession` per recorded question. `LiveTranscriptionSTT`
adapts the existing `gpt-live-transcribe` transport without changing its model.
The microphone still stops on Send/end-of-turn. Both OpenAI paths now use the
local SmolLM2 completion scorer: stable text can finish after 600 ms of acoustic
silence; uncertain, unavailable, or slow scoring falls back to the 1.5-second
silence deadline. Late transcript updates do not restart that deadline. Inference
runs off the audio event loop; resumed speech invalidates stale decisions.
Per-connection context and turn resets survive a normal final transcript without
closing the provider socket, preparing the transport for continuous capture.
The UI and LiveKit turn wrapper are still one-shot; this is not the full proposed
continuous turn coordinator or interruption gate. LiveKit's implicit VAD is
disabled in this manual-turn path to avoid a competing endpoint controller.

`/api/v1/voice/livekit/session` transfers native PCM, transcript events, an
accepted question, the validated repository answer, and synthesized audio over
one authenticated loopback connection. Repository work reuses `ask_repository()`;
`SentiaNarrationAgent.llm_node()` only paraphrases its answer with Cerebras.
`tts_node()` performs chunk-safe pronunciation replacement. Native device exit
acknowledges output completion; synthesis completion alone does not.

`sentia.voice.openaiPipeline=livekit` selects this path (default on this branch);
`native` selects the original OpenAI path with the same silence timing. Deepgram
retains Flux turn detection with a 1.5-second silence timeout. Keys are read
locally from `apps/sidecar/.env` on development startup. See
[OpenAI setup](../README.md#openai-voice-setup). The Deepgram initial-stack choice
and persistent/interruption sections below remain later-phase proposals, not
claims about this implemented OpenAI phase.

Confirmed product decision: Claude/Codex remains responsible for understanding
the repository. Cerebras turns its evidence-backed answer into spoken English;
it does not independently answer code questions or run coding tools.

This updates the [continuous conversation contract](VOICE_CONVERSATION_ARCHITECTURE.md).
In particular, LiveKit in the Python sidecar becomes the turn-taking authority,
replacing the proposed extension-owned conversation state machine.

## The flow in plain English

```text
Persistent microphone
  -> STT: turn speech into words
  -> LiveKit: decide when the user has finished
  -> Claude/Codex: inspect the repository and produce an evidenced answer
  -> Publish that written answer and its evidence in the editor
  -> Cerebras: stream a concise, natural explanation of that answer
  -> Pronunciation filter: Sentia becomes "Sen-shia" for speech only
  -> TTS: turn each ready sentence into audio
  -> Native speaker: play the audio and report actual playback progress
  -> Return to Listening, without restarting the microphone
```

Input continues during both analysis and playback. A confirmed interruption
gives the user the floor and invalidates the old response's outstanding work.

The active editor file still supplies extra transcription vocabulary, not a
restriction on which repository files Claude/Codex can inspect. Cerebras receives
the validated answer, not unrestricted repository access.

## Provider responsibilities

| Job                       | Initial branch choice                                                     | Reason / alternative                                                                                                                                        |
| ------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Conversation coordination | LiveKit Python `AgentSession` in the local sidecar                        | Owns turn-taking and the listening/thinking/speaking lifecycle.                                                                                             |
| Speech recognition        | Deepgram Flux through `deepgram.STTv2(model="flux-general-en")`           | Reuses the existing provider family and its built-in turn detection. Nova-3 is an alternative to benchmark, not a Flux replacement with identical behavior. |
| Repository analysis       | Existing selected Claude or Codex provider                                | Keeps repository inspection and evidence validation intact.                                                                                                 |
| Spoken wording            | `openai.LLM.with_cerebras(model="gpt-oss-120b", ...)`                     | Cerebras is an LLM service; the LiveKit OpenAI plugin provides the compatible client.                                                                       |
| Audio synthesis           | Deepgram `deepgram.TTS(model="aura-2-thalia-en")` as a starting candidate | Uses the Deepgram key; benchmark against the current voice before changing user defaults. OpenAI TTS remains an alternative.                                |

Flux and Nova use different plugin classes; see the
[Deepgram plugin guide](https://docs.livekit.io/agents/models/stt/deepgram/).
The Cerebras helper is documented in the
[OpenAI plugin reference](https://docs.livekit.io/reference/python/livekit/plugins/openai/index.html).
Cerebras supplies neither STT nor TTS in this pipeline; see its
[LiveKit integration](https://inference-docs.cerebras.ai/integrations/livekit).

OpenAI recognition remains a supported architecture path, but not a promised
one-line swap for the current Python realtime implementation. LiveKit's Python
STT guide shows `gpt-4o-mini-transcribe`; its realtime STT options differ by SDK.
Whisper is a useful comparison path, not our assumed low-latency winner. See the
[OpenAI STT plugin guide](https://docs.livekit.io/agents/models/stt/openai/).

For the existing `gpt-live-transcribe` path, evaluate a LiveKit-compatible STT
adapter. It needs application-managed audio commits and per-item ordering, not
OpenAI server turn detection. Keep its 24 kHz PCM contract; do not apply the
cookbook's Whisper sample-rate advice to every provider. See
[OpenAI realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription).

## Local integration, not a mandatory cloud room

Start with LiveKit Agents as the orchestration library inside Sentia's existing
local Python sidecar. Supply custom audio input/output adapters over the private,
authenticated loopback connection to the extension's native audio helper.

This is a design choice based on `AgentSession.start()` accepting no room and
custom `session.input.audio` / `session.output.audio`. It must pass an integration
spike with a pinned SDK before becoming the runtime path. See the
[session API](https://docs.livekit.io/reference/python/livekit/agents/voice/index.html).

There is no need to move repository analysis to a cloud worker just to use the
library. LiveKit Cloud rooms, inference services, or cloud-backed adaptive
interruptions are separate options; enabling them adds credentials, costs, and
deployment decisions. Do not silently enable them.

## One owner for each responsibility

| Component                                     | Responsibility                                                                                                         |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Proposed `LiveKitVoiceSession` in the sidecar | Own one persistent `AgentSession`, provider selection, turn IDs, cancellation, and bounded conversation context.       |
| Proposed `SentiaVoiceAgent`                   | Connect accepted user turns to repository analysis, then Cerebras narration.                                           |
| Extension bridge                              | Start/end the conversation, transfer audio, store credentials, mirror state; no competing transcript-submission timer. |
| Webview                                       | Display state, transcript, written answer, and evidence; send user commands.                                           |
| Duplex native audio helper                    | Capture and play simultaneously, suppress echo, pause/resume, and acknowledge device playback.                         |

The SDK handles its conversation state. Sentia's wrapper retains session/turn/
response-generation IDs to reject stale results; it does not implement a second
independent end-of-turn detector. A workspace/provider change ends or resets the
conversation so the next turn cannot inherit the wrong repository context.

## Connecting code analysis to speech

Use a custom `SentiaVoiceAgent.llm_node()` for the response pipeline rather than
letting a stock agent send the original repository question directly to Cerebras.
LiveKit supports custom generation and synthesis nodes; see
[agent nodes](https://docs.livekit.io/agents/logic/nodes/).

1. Capture the accepted question, selected analysis provider, workspace identity,
   snapshot revision, and bounded follow-up context.
2. Reuse the analysis and evidence-validation path behind `ask_repository()` and
   `RepositoryIntelligenceRouter.answer()`. Factor reusable service code out of
   the HTTP handler; do not bypass workspace-trust or evidence checks.
3. Send the existing structured `RepositoryAnswer` to the editor as soon as it
   is ready. Display its original wording and citations.
4. Give Cerebras the question and validated answer, with an instruction to
   explain only those facts in concise conversational English. No tool access,
   new code claims, or coding actions. Treat repository content as data, not as
   instructions for the narration model.
5. Stream narration into TTS in bounded sentence groups. In `tts_node()`, apply
   the equivalent of `prepareSpeechText()` before synthesis. Buffer word/sentence
   boundaries so a provider chunk split such as `Sen` + `tia` is still handled.
6. Tag text, audio, and completion events with response-generation IDs. On
   cancellation, close provider streams and discard late results before they
   can reach the speaker or modify the current answer.

The model's narration is a paraphrase, not a newly validated code answer. Tests
must check factual preservation. The original written answer stays authoritative.

The existing `spokenAnswer` is a fallback if Cerebras fails before any audio
starts. Once speech has started, do not automatically replay the entire fallback:
report the failure and offer replay, avoiding duplicate or contradictory speech.

## Unified turn detection and persistent listening

Deepgram Flux: use its STT turn boundary with
`TurnHandlingOptions(turn_detection="stt")`. OpenAI/Nova: use a speech detector
(such as Silero) plus a compatible local completion detector after verifying
SDK and transcript compatibility. Keep cloud inference out of the default local
configuration. Both paths emit the same accepted-turn contract to Sentia.
The distinction between STT endpointing and client detection is documented in
[LiveKit turn handling](https://docs.livekit.io/agents/logic/turns/).

An STT final fragment alone is not permission to ask the repository question.
Only an accepted user turn triggers analysis. Preserve resumption/grace handling
from the original contract; do not stack its old timers on top of SDK endpointing.

Keep one session and one microphone stream across multiple turns. Remove the
webview's two-second auto-stop behavior in continuous mode. Explicit Stop ends
the conversation; finishing a sentence does not.

Mirror SDK state events to the webview, but verify actual device playback:
Listening means ready for a turn, Thinking means analysis or speech preparation,
and Speaking means audio is playing. Synthesis completion is not playback
completion. The custom output adapter reports segment completion using
`on_playback_finished()` only after the native device completes or interrupts
that segment. It must implement real pause support before advertising
`can_pause`. See the [audio output API](https://docs.livekit.io/reference/python/livekit/agents/voice/io.html).

## Intelligent interruption

Echo suppression comes first: the microphone must not interpret Sentia's own
speaker output as a new user question. Upgrade the separate native helpers to
one duplex engine with voice processing; test headphones and speakers separately.

Use speech duration plus actual recognized words to qualify local interruptions.
Start with the original contract's tunable two-word / 300 ms candidate gate,
stable text, and explicit single-word controls such as "stop". Coughs, noise,
and unstable fragments must not directly invoke speaker stop. These are starting
thresholds, not a guarantee of perfect classification.

Disable competing automatic interruption triggers when testing a custom gate;
only one policy may give the floor to the user. Preserve buffered user audio
while qualifying speech. A gate-only cancellation must not submit a second
duplicate turn through the normal session path.

LiveKit's false-interruption recovery helps resume mistakenly paused speech,
but recovery after a pause is not the same as preventing that pause. Its adaptive
interruption feature is cloud-backed and has development usage limits; evaluate
it separately rather than assuming it is free local functionality. See
[adaptive interruption handling](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/).

Pause/resume capability belongs in the native output adapter, with a saved
playback cursor. Keep the original contract's distinction between temporarily
pausing and abandoning an answer when the user asks a new question.

## Credentials and privacy

- Required for the initial direct-plugin pipeline: a Cerebras key and a Deepgram
  key, plus the existing Claude/Codex connection.
- Optional: an OpenAI key for OpenAI recognition or synthesis.
- LiveKit URL/API key/API secret are required only if the selected implementation
  uses a server/Cloud feature; they are not assumed for custom local audio I/O.
- Add a Connect Cerebras action following the current SecretStorage pattern.
  Keys go through VS Code's secure prompt, never chat, repository files, webview
  messages, transcripts, or logs. Keep keys in sidecar memory and wipe on disconnect.
- Cerebras receives repository-derived answer content. Disclose this extra
  provider boundary when connecting, retain existing secret redaction, and send
  the minimum required answer context. Disable SDK recording by default.

## Latency: what this can and cannot improve

Today Claude/Codex already produces `spokenAnswer` with the written answer.
Cerebras therefore adds a language-model step; it is not automatically faster
than the existing path. Streaming allows Cerebras, TTS, and playback to overlap,
but the first grounded spoken sentence still waits for repository analysis.

Record separate timestamps for user speech end, accepted turn, STT final text,
repository answer ready, first Cerebras text, first TTS audio, and first device
playback. Compare end-of-user-speech to first audible grounded answer, not just
Cerebras token generation. Measure median and p95 on the same questions and audio.
The quoted cookbook latency numbers are not measured Sentia performance.

Benchmark the current same-pass `spokenAnswer` path against Cerebras narration.
Only then consider changing analysis prompts to omit duplicate speech generation;
that would require preserving protocol compatibility and a reliable fallback.

## Implementation order and checks

1. Pin compatible LiveKit/plugin versions and prove custom local audio I/O,
   Flux turn events, pause support, and clean shutdown. Keep the current runtime
   available while this spike is evaluated.
2. Add secure Cerebras connection and a narration-only streaming service. Test
   grounded paraphrases, pronunciation across chunks, timeout, cancellation,
   fallback, and typed-answer behavior (no unsolicited speech).
3. Integrate `SentiaVoiceAgent` with the existing repository service. Test evidence
   publication, follow-up context, provider changes, and stale answer rejection.
4. Upgrade native duplex audio and protocol acknowledgments. Test actual playback
   completion, queue bounds, echo, and device changes.
5. Enable persistent sessions and one interruption policy. Test ten consecutive
   turns, pauses mid-sentence, repeated coughs, fragments, backchannels, deliberate
   interruption, explicit stop, and no duplicate question submissions.
6. Add the OpenAI recognition path through the same contract, then compare full
   latency and interruption behavior. Do not declare improvement without measurements.

Primary integration points are `App.startVoice()` / `submitVoiceTurn()`,
`SidecarRuntime.startVoice()`, `SentiaViewProvider.handleMessage()`,
`app.py:ask_repository()`, `voice.py`'s provider adapters, and
`NativeMicrophone` / `NativeSpeaker`. The old direct TTS branches must not play
alongside the new session: select exactly one speech pipeline per response.
