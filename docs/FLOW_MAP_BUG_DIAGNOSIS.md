# Flow Map bug diagnosis

Status: **Deferred**

Flow Map development is currently paused while product work moves to the seamless
conversation experience. This document preserves the latest diagnosis so the work
can be resumed without repeating the investigation.

## Summary

Sentia can identify several components related to a feature, but it cannot always
turn them into a complete, connected explanation of how the feature works. The main
problem is not simply answer quality. Important limitations exist in retrieval,
source inspection, language coverage, relationship extraction, and cross-system
data-flow tracing.

## Actual issues

1. **Sentia investigates weakly related code because retrieval relies too heavily
   on matching words.** The correct move would be to prioritize code that is
   structurally connected to the requested feature.

2. **Sentia can know that a component exists without reading its implementation
   because the structural index and investigation process do not hand discoveries
   off reliably.** The correct move would be to automatically inspect important
   components discovered by the index.

3. **Sentia can miss an important implementation because earlier candidates use
   the available evidence budget first.** The correct move would be to reserve
   evidence capacity for explicitly requested reads, unresolved concepts, and
   strongly connected components.

4. **Sentia mainly traces Python because JavaScript and TypeScript are not yet part
   of the structural feature-trace index.** The correct move would be to index and
   trace frontend components, state changes, API calls, routing, and backend code
   together.

5. **Sentia struggles when a feature crosses an application boundary because it
   mainly understands direct code relationships.** The correct move would be to
   support evidence-backed handoffs through HTTP requests, events, queues,
   databases, files, and shared state.

6. **Sentia can find the beginning and end of a feature without explaining what
   connects them because it does not track important values across boundaries.**
   The correct move would be to follow values through function arguments, API
   responses, storage, later requests, and downstream consumers.

7. **Sentia can produce stages with no transitions because it selects individually
   relevant components before proving that they form one continuous path.** The
   correct move would be to validate a connected input-to-outcome journey before
   presenting the components as a flow.

8. **Sentia can treat alternative implementations as stages in the primary flow
   because it does not separate the main path from optional or competing paths
   clearly enough.** The correct move would be to show one primary flow and place
   alternatives in separately labelled branches.

9. **Sentia can report behavior as missing even when it exists because it may stop
   before inspecting a referenced helper, serializer, service, handler, or
   configuration file.** The correct move would be to follow relevant dependencies
   until the behavior is found or its absence is established.

10. **Sentia can miss real entry points because routes, commands, registrations,
    and framework configuration are not always resolved to their handlers.** The
    correct move would be to use framework-aware rules to connect each trigger to
    the code that handles it.

11. **Sentia can stop at a response because it cannot follow what another subsystem
    does with that response.** The correct move would be to continue into the next
    consumer, such as a frontend, worker, webhook handler, or external integration.

12. **Sentia can miss behavior that happens later because separate operations do
    not appear in a single call chain.** The correct move would be to represent
    lifecycle relationships such as "created now, stored, and consumed later."

13. **Sentia may search repeatedly for information its current index cannot supply
    because capability limits are not applied early enough.** The correct move
    would be to detect unsupported languages or frameworks before spending the
    investigation budget and clearly narrow the claimed scope.

14. **Sentia can let ambiguous wording distract from an otherwise clear request
    because possible interpretations are treated as equally important.** The
    correct move would be to proceed with the most likely interpretation and keep
    the ambiguity as a separate note or clarification.

15. **Sentia can be technically cautious but practically unhelpful because it
    removes connections that its current relationship model cannot prove.** The
    correct move would be to add explicit, evidence-backed relationship types for
    execution calls, HTTP handoffs, state changes, and data or credential handoffs.

16. **Sentia can overlook defects that prevent a mapped feature from running
    because flow mapping and code diagnostics are separate.** The correct move
    would be to report obvious blocking defects found in the inspected code without
    turning the flow map into a general-purpose code review.

17. **Sentia can use many model tokens without producing a complete result because
    too much broad evidence is sent to the model.** The correct move would be to do
    more ranking, dependency expansion, filtering, and evidence selection locally.

## Important design distinction

A useful Flow Map needs more than call-graph edges. It should distinguish at least
these connection types:

- **Execution:** one function, method, task, or handler invokes another.
- **Registration:** configuration connects a route, event, command, or task to a
  handler.
- **HTTP handoff:** one subsystem sends a request that another subsystem handles.
- **State handoff:** one operation writes state that another operation reads later.
- **Data handoff:** an important value is returned, stored, transformed, and later
  consumed.

These relationships must remain source-backed and visibly labelled. Adding them
would allow Sentia to produce a connected feature explanation without pretending
that every connection is a direct function call.

## Recommended order when work resumes

1. Fix evidence prioritization and guarantee explicit reads.
2. Improve local dependency and receiver-call resolution.
3. Separate primary flows from alternative branches.
4. Add JavaScript and TypeScript entities and relationships.
5. Add cross-language HTTP matching.
6. Add state and data-handoff tracing.
7. Add regression scenarios that require a connected input-to-outcome result.
8. Measure completeness, irrelevant evidence, token use, and latency together.

## Current product decision

Do not continue expanding Flow Maps during the current product phase. Keep this
diagnosis as deferred engineering work and prioritize the seamless conversation
experience.
