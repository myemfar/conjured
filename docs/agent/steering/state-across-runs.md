---
kind: steering
audience: [agents]
slug: steering-state-across-runs
renders_from: inputs-outputs/carrying-a-value-across-runs
---

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
