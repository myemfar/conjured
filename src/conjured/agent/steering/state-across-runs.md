---
kind: steering
audience: [agents]
slug: steering-state-across-runs
renders_from: inputs-outputs/carrying-a-value-across-runs
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#steering-state-across-runs}
# Steering — carrying a value across runs

**When this fires:** you want a value to persist from one pipeline invocation to the
next (an evolving mood, a running summary) and are about to declare the same channel as
both a read and a write of one handler, or reach for a module-level variable, a class
attribute, or any other in-process store.

**Do this:** carry the value across the `inputs` / `outputs` API boundary — write the
carried-forward value to a declared `outputs` channel, read it off the RunResult, seed
it back as an `inputs` channel on the next invocation. Channels are single-assignment
within a run, and the engine holds nothing between runs — the feedback path is the
consumer's, and that is the designed shape, not a workaround.

The owning canonical statement:

Channels are
[single-assignment](#kernel-semantics) — a run never mutates a channel value in place — so
a value that must persist from one invocation to the next (an NPC's evolving mood; a running
summary) is carried across the `inputs` / `outputs` boundary: the graph writes the
carried-forward value to a declared `outputs` channel (e.g. `mood_next`), the consumer reads
it off the [RunResult](#pipeline-result-runresult), and seeds it back as an `inputs` channel
on the next invocation. The engine holds nothing between runs
([§ Kernel semantics](#kernel-semantics)); the feedback path is the consumer's, threaded
through this API boundary — which is why a within-run loop that would rewrite one channel is
instead a next-channel the consumer feeds forward.
