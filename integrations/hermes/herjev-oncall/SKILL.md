---
name: herjev-oncall
description: On-call workflow for log incidents detected by herjev (warn/error/fatal/panic on watched servers) — explain an alert, look up incidents, analyze pasted logs, mute noise.
version: 0.3.1
metadata:
  hermes:
    tags: [sre, oncall, logs, observability]
    category: devops
---
# herjev on-call

herjev watches servers' logs. Each alert already carries a Jev triage verdict
(notify, urgency, category) and an AI analysis (summary, probable causes,
actions, diagnostic commands). herjev itself delivers alerts to every channel
(Teams included, and this chat through a webhook route that posts herjev's text
as it is); this skill handles the conversation around them. Use it when the
user replies to an alert, asks what is broken, or pastes logs.

An alert reads:

    loại: fatal
    thời gian: 21:06:15 30/10
    nội dung: <the log line>
    service: <service>
    root cause dự đoán: <herjev's probable cause>

herjev is reached through its MCP server `herjev` (tools `mcp__herjev__*`). There
is no shell: never try to run herjev or other commands in a terminal.

## When to Use
- The user replies to or asks about an alert in the format above.
- "what happened on <host/service>?", "any incidents tonight?", "why is X panicking?"
- The user pastes log lines and asks for help.
- The user wants to silence a noisy alert.

## Tools
| Need | Tool |
|---|---|
| Is monitoring alive? | `mcp__herjev__status` |
| Recent incidents | `mcp__herjev__incidents` (`limit`, `hours`, `min_level`, `notified_only`) |
| One incident in full | `mcp__herjev__incident` (`id`: incident id or fingerprint) |
| Triage pasted logs | `mcp__herjev__analyze` (`logs`, optional `host`, `service`) |
| Mute noise | `mcp__herjev__mute` (`id`, `minutes`, `reason`) |

## Procedure
1. **Alert text as the whole message** (a webhook route without a chat runs
   this skill on the bare alert): reply with that text exactly as given and
   nothing else. Do not call any tool; the deep dive waits until the user asks.
2. **Orient**: if the user asks whether monitoring works, call
   `mcp__herjev__status`; otherwise call `mcp__herjev__incidents` and match the
   alert they mean by its time, service and log line.
3. **Deep dive**: call `mcp__herjev__incident` with that incident's id (or the
   fingerprint from an `open:` link). Read `state.samples` and
   `state.context`; the context lines *before* the error usually reveal the trigger.
4. **Think, don't just repeat**: start from the stored analysis but check it
   against the raw lines. Point at the log line behind each cause. If the
   evidence is thin, say which extra data would settle it.
5. **Advise**: mitigation first (restart/rollback/scale/failover), then the
   fix, then prevention, with the diagnostic commands for the user to run.
6. **Pasted logs**: pass them to `mcp__herjev__analyze`.
7. **Noise**: if the user says an alert is expected, offer to mute it (default
   60 minutes) and suggest a lasting fix (log level, herjev `guidance`).
   Call `mcp__herjev__mute` only after the user confirms.

## Style
Short and phone-readable, in the user's language. Webhook alerts are relayed
verbatim (step 1). Never invent log lines.
