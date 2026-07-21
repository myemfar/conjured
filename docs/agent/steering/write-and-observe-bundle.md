---
kind: steering
audience: [agents]
slug: steering-write-and-observe-bundle
renders_from: adding-a-new-kind/write-and-observe-bundle
---

{#steering-write-and-observe-bundle}
# Steering — write-and-observe is a [handler + companion hook] pair

**When this fires:** you want one handler to both write channels AND emit to an
observability destination (a metrics endpoint, a log service), and are about to give a
transform or service a side-channel emission — or invent a dual-role handler.

**Do this:** compose a **pair of existing kinds** — the channel-writing handler
(transform or service) followed by a companion hook that reads the written channel and
emits. Declare the pair once as a bundle TOML and embed it wherever the pair is wanted.
A hook writes no channels and a channel-writing kind's emissions are exactly its
declared writes; keeping the two roles in two nodes is the designed shape, never a
limitation to engineer around.

The owning canonical statement:
