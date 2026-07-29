# Sentia MVP Product Contract

## Product boundary

Sentia is an installable, editor-native VS Code extension. React renders its
sidebar webview, and a supervised FastAPI sidecar performs local indexing and AI
orchestration. Neither component is deployed as a public website or hosted
Sentia service during the MVP.

## Primary user journey

1. The developer installs Sentia and opens a trusted local Git repository.
2. The extension opens the Sentia sidebar and starts its compatible local
   sidecar on loopback using a short-lived token.
3. Sentia prompts the developer to choose Claude Code or Codex, connects the
   selected provider, and remembers that choice.
4. Sentia inventories and indexes the repository while showing visible progress.
5. The developer types “What are you?” or records it with Sentia's Flux voice
   control.
6. Sentia answers in plain language and attaches navigable file and line evidence.
7. The developer selects code and requests a simple or line-by-line explanation.
8. Sentia explains the selection and highlights related source ranges.
9. The developer brainstorms a change in read-only Brainstorm mode.
10. The developer switches to Plan & Build mode and asks Sentia to produce a structured
    implementation plan.
11. Sentia displays the exact plan revision, expected files, commands, risks, and
    verification steps. Nothing executes yet.
12. The developer approves, revises, or rejects that plan.
13. After approval, the selected coding adapter starts a bounded run.
14. Sentia displays observable tool activity, navigates to relevant files, and
    narrates only verified milestones.
15. The developer can stop speech or cancel coding independently at any time.
16. Sentia presents the run-owned diff, commands, tests, remaining concerns, and
    audit history without staging or committing changes.

## MVP users

- A single developer
- One trusted local workspace at a time
- One active coding run per workspace
- VS Code stable as the primary editor host

## MVP non-goals

- A standalone website, browser product, or hosted Sentia backend
- Sentia accounts, teams, billing, or cloud sync
- Remote repository hosting or unattended remote execution
- Parallel coding agents or automatic worktree management
- Automatic commits, pushes, or pull requests
- Undocumented Cursor APIs or screen-scraping editor automation
- Persisted microphone recordings or provider credentials in the webview

## Product invariants

- Auto mode is the default. The model recommends Brainstorm or Plan & Build for
  each request, explains the choice, and yields to an explicit user override.
- Prompting, Implementing, Testing, and Review are derived from observable
  sidecar and coding-agent events; the model cannot claim those states merely by
  selecting a mode.
- Brainstorm mode cannot edit files or execute commands.
- Plan & Build mode cannot prompt a coding agent until a displayed plan revision
  is approved.
- The UI visibly distinguishes Brainstorm, Plan mode, Prompting, Implementing,
  Testing, and Review.
- Repository facts require validated evidence; guesses are labeled as inference.
- Live status is derived from observable events, not hidden model reasoning.
- The webview never receives direct filesystem, process, or sidecar credentials.
- The extension owns and authenticates sidecar transport and process lifecycle.
- Deepgram credentials remain in VS Code SecretStorage and trusted local process
  memory; microphone and synthesized audio stream through the extension boundary
  and are not stored.
- A spoken answer is derived from the completed, validated display answer in a
  second AI pass; it preserves the same claims and guidance while citations,
  paths, Markdown, and raw code notation are never read aloud.
- Sentia never silently stages, commits, pushes, or discards user changes.
