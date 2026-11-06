# Claude Code cost and token metrics per session (#25)

Research for #25 (`tower stats`: metrics per merged PR, computed from tower's local event log of Claude Code hook events). The question: can a local, standard-library-only Python tool attribute model cost to a Claude Code session, and through the session to a git branch? Checked **2026-11-06** against the official Claude Code docs only (the Markdown builds of the pages, e.g. `https://code.claude.com/docs/en/monitoring-usage.md`). Installed version on this machine at check time: `2.1.304`. The docs change often; re-check before relying on version-gated details.

Sources:

- Monitoring (OpenTelemetry): https://code.claude.com/docs/en/monitoring-usage
- Hooks reference: https://code.claude.com/docs/en/hooks
- Customize your status line: https://code.claude.com/docs/en/statusline
- Manage costs effectively: https://code.claude.com/docs/en/costs
- Sessions (transcripts, script interfaces): https://code.claude.com/docs/en/sessions
- Settings reference (`modelPricing`): https://code.claude.com/docs/en/settings-reference#modelpricing
- CLI reference: https://code.claude.com/docs/en/cli-reference

One section (§3.3, transcript shape) adds a local observation of a transcript file on this machine, because the docs deliberately leave that format undocumented. It is marked as such.

## TL;DR

- **OpenTelemetry has exactly what we want, but only over a network exporter.** `claude_code.cost.usage` (USD) and `claude_code.token.usage` (tokens) carry `session.id`, and the `claude_code.api_request` event carries `cost_usd` and per-type token counts per request. No attribute carries cwd or branch; the closest is opt-in `vcs.*` repository identity (repo, not branch).
- **No file exporter.** Exporters are `otlp`, `prometheus`, `console` (metrics) and `otlp`, `console` (logs). Getting the data into a file means running *something* that listens: an OTLP receiver (could be a small stdlib `http.server` accepting `http/json`) or a Prometheus scraper. The one file-writing option, `OTEL_LOG_RAW_API_BODIES=file:<dir>`, dumps whole conversations and is far too invasive for this.
- **Hooks carry no running session cost.** SessionStart (on resume/fork) and PreModelSwitch carry `estimated_cache_write_usd`, a *forecast*, not spend. Stop and SessionEnd carry no cost or token fields. Every hook does carry `session_id`, `cwd` and `transcript_path`.
- **The status line JSON carries `cost.total_cost_usd` and `session_id`** (plus `cwd`, `workspace.git_worktree`, `worktree.branch`), but only while a status line command is configured, and the user has exactly one.
- **Bottom line: read the transcript at SessionEnd (or lazily at `tower stats` time).** Transcript entries carry `message.usage` per API response and, in practice, `sessionId`, `cwd` and `gitBranch`. Cost = tokens x a price table tower keeps. Caveats: the format is officially "internal" and unstable, usage lines repeat per content block (dedupe by `requestId`), subagent usage lives in separate files, and tower's dollars will be a list-price estimate. If tower ever wants Claude Code's own cost figure, it needs a collector or a status line wrapper.

## 1. OpenTelemetry: names, attributes, units

### Metrics

"The Unit column shows the OpenTelemetry unit string attached to each metric; count metrics carry none." ([monitoring-usage#metrics](https://code.claude.com/docs/en/monitoring-usage#metrics))

| Metric | Docs description | Unit |
| :- | :- | :- |
| `claude_code.cost.usage` | "Cost of the Claude Code session" | `USD` |
| `claude_code.token.usage` | "Number of tokens used" | `tokens` |
| `claude_code.session.count` | "Count of CLI sessions started" | none |
| `claude_code.commit.count` | "Number of git commits created" | none |
| `claude_code.pull_request.count` | "Number of pull requests created" | none |
| `claude_code.active_time.total` | "Total active time" | `s` |

- Both cost and token counters are "Incremented after each API request." ([monitoring-usage#cost-counter](https://code.claude.com/docs/en/monitoring-usage#cost-counter), [#token-counter](https://code.claude.com/docs/en/monitoring-usage#token-counter))
- Cost counter attributes beyond the standard ones: `model`, `query_source` ("One of `"main"`, `"subagent"`, or `"auxiliary"`"), `speed`, `effort`, `agent.name`, `skill.name`, `plugin.name`, `marketplace.name`, `mcp_server.name`, `mcp_tool.name`. ([monitoring-usage#cost-counter](https://code.claude.com/docs/en/monitoring-usage#cost-counter))
- Token counter adds `type`: "(`"input"`, `"output"`, `"cacheRead"`, `"cacheCreation"`). The `"input"` type excludes tokens read from or written to the prompt cache". ([monitoring-usage#token-counter](https://code.claude.com/docs/en/monitoring-usage#token-counter))
- Temporality defaults to delta: "`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | Metrics temporality preference (default: `delta`)". A receiver must sum data points, not take the last one. ([monitoring-usage#common-configuration-variables](https://code.claude.com/docs/en/monitoring-usage#common-configuration-variables))
- Prometheus quirk: "When `prometheus` is the only exporter listed in `OTEL_METRICS_EXPORTER`, Claude Code omits the `USD`, `tokens`, and `s` units from the exported metrics". ([monitoring-usage#metrics](https://code.claude.com/docs/en/monitoring-usage#metrics))
- "Cost metrics are approximations. For official billing data, see [Manage costs for your organization]". ([monitoring-usage#cost-monitoring](https://code.claude.com/docs/en/monitoring-usage#cost-monitoring))

### The `claude_code.api_request` event

"Logged for each API request to Claude." Exported only "when `OTEL_LOGS_EXPORTER` is configured". ([monitoring-usage#api-request-event](https://code.claude.com/docs/en/monitoring-usage#api-request-event), [#events](https://code.claude.com/docs/en/monitoring-usage#events))

| Attribute | Docs quote |
| :- | :- |
| `cost_usd` | "Estimated cost in USD" |
| `cost_usd_micros` | "Estimated cost in millionths of a US dollar, emitted as an integer" |
| `input_tokens` | "Number of input tokens, excluding tokens read from or written to the prompt cache" |
| `output_tokens` | "Number of output tokens" |
| `cache_read_tokens` | "Number of tokens read from cache" |
| `cache_creation_tokens` | "Number of tokens used for cache creation" |
| `cache_creation_5m_tokens`, `cache_creation_1h_tokens` | split by cache lifetime; "Requires Claude Code v2.1.300 or later" |
| `model`, `duration_ms`, `request_id`, `query_source`, `speed`, `effort` | per-request context |

### Standard attributes: session yes, cwd and branch no

"All metrics and events share these standard attributes" ([monitoring-usage#standard-attributes](https://code.claude.com/docs/en/monitoring-usage#standard-attributes)):

| Attribute | Docs quote | Default |
| :- | :- | :- |
| `session.id` | "Claude Code's own session ID, which `/clear` replaces" | on (`OTEL_METRICS_INCLUDE_SESSION_ID` default `true`) |
| `vcs.repository.url.full`, `vcs.owner.name`, `vcs.repository.name`, `vcs.provider.name` | "The session repository's identity, derived from its `origin` remote" | off (`OTEL_METRICS_INCLUDE_REPOSITORY` default `false`); "Requires Claude Code v2.1.269 or later" |
| keys from `OTEL_RESOURCE_ATTRIBUTES` | "Custom attributes you set, such as `department` or `team.id`" | on |
| `user.*`, `organization.id`, `terminal.type`, `app.*` | identity and client | mixed |

- There is no cwd, worktree or branch attribute on metrics or on `api_request`. The `vcs.*` set identifies the repository only; every worktree of tower would report the same values.
- A branch name appears only on `tool_result` for a successful `git commit`, and only with `OTEL_LOG_TOOL_DETAILS=1`: "`vcs.ref.head.name` is the branch it was committed on". ([monitoring-usage, tool result event](https://code.claude.com/docs/en/monitoring-usage#tool-result-event))
- So the join to a branch has to go through `session.id` and tower's own event log (hook `cwd`), not through OTel attributes. `session.id` matches hook `session_id` (the hooks page says hook `prompt_id` "Matches the `prompt.id` attribute on OpenTelemetry events", [hooks#common-input-fields](https://code.claude.com/docs/en/hooks#common-input-fields)).
- Workaround that would not work: setting a per-worktree `OTEL_RESOURCE_ATTRIBUTES=branch=...` in the repo's `.claude/settings.json`. "Claude Code ignores the OpenTelemetry exporter variables in a repository's `.claude/settings.json` and `.claude/settings.local.json`". ([monitoring-usage#administrator-configuration](https://code.claude.com/docs/en/monitoring-usage#administrator-configuration)) (That sentence names exporter variables; whether `OTEL_RESOURCE_ATTRIBUTES` is in the ignored list was not checked.)

## 2. Exporters and env vars

| Variable | Docs quote |
| :- | :- |
| `CLAUDE_CODE_ENABLE_TELEMETRY` | "Enables telemetry collection (required)" — `1` |
| `OTEL_METRICS_EXPORTER` | "Metrics exporter types, comma-separated" — `console`, `otlp`, `prometheus`, `none` |
| `OTEL_LOGS_EXPORTER` | "Logs/events exporter types, comma-separated" — `console`, `otlp`, `none` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | "Claude Code has no default protocol, so set this or the signal-specific protocol variable for each `otlp` exporter you enable" — `grpc`, `http/json`, `http/protobuf` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | "OTLP collector endpoint for all signals" — e.g. `http://localhost:4317` |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` / `..._LOGS_ENDPOINT` | per-signal, e.g. `http://localhost:4318/v1/metrics`, `http://localhost:4318/v1/logs` |
| `OTEL_METRIC_EXPORT_INTERVAL` | "Export interval in milliseconds (default: 60000)" |
| `OTEL_LOGS_EXPORT_INTERVAL` | "Logs export interval in milliseconds (default: 5000)" |

All from [monitoring-usage#common-configuration-variables](https://code.claude.com/docs/en/monitoring-usage#common-configuration-variables).

- **Prometheus** is pull-based: "For Prometheus, scraped from `http://localhost:9464/metrics`". Something has to scrape it while the session is alive. ([monitoring-usage#example-configurations](https://code.claude.com/docs/en/monitoring-usage#example-configurations))
- **Console**: the docs list it "For console debugging" but don't say where it writes. Not a usable sink for an interactive session.
- **No file exporter** is listed for metrics or logs.
- **The only file-writing knob** is `OTEL_LOG_RAW_API_BODIES=file:<dir>`: "Claude Code writes untruncated bodies to `.request.json` and `.response.json` files under that directory", and appends to `<dir>/index.jsonl` lines with "`timestamp`, `session_id`, `query_source`, `model`, `request_id`, `message_id`, `message_uuid`, `request_file`, and `response_file`". But "The bodies contain the full conversation history, including the system prompt, every prior user and assistant turn, and tool results". The docs don't say whether it works without a logs exporter. Rejected: a full conversation dump to get one number. ([monitoring-usage, raw API bodies](https://code.claude.com/docs/en/monitoring-usage#api-response-body-event))
- **Env vars don't reach hooks**: "Claude Code doesn't pass `OTEL_*` environment variables to the subprocesses it spawns, including the Bash tool, hooks". ([monitoring-usage#administrator-configuration](https://code.claude.com/docs/en/monitoring-usage#administrator-configuration))
- **Where they must be set**: "Set them in managed settings, or have each developer set them in their shell or `~/.claude/settings.json`." A tower install would have to edit the user's global settings, which tower's own rules avoid.

Minimal collector-free-ish option, if ever wanted: `OTEL_METRICS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/json`, endpoint `http://127.0.0.1:<port>`, and a long-running stdlib `http.server` that appends each POST body to a JSONL file. That is a collector in all but name, and it loses data whenever it isn't running.

## 3. Cost and tokens outside OpenTelemetry

### 3.1 Hook stdin

Every hook gets "`session_id`", "`transcript_path`" ("Path to conversation JSON. The transcript file is written asynchronously and may lag the in-memory conversation") and "`cwd`". ([hooks#common-input-fields](https://code.claude.com/docs/en/hooks#common-input-fields))

| Hook | Cost / token fields | Docs quote |
| :- | :- | :- |
| SessionStart | `context_tokens`, `estimated_cache_write_usd` | only "When `source` is `"resume"` or `"fork"` and the transcript contains at least one response from Claude"; "Estimated cost in US dollars of writing `context_tokens` to the prompt cache on the session's model, excluding the response" ([hooks#sessionstart-input](https://code.claude.com/docs/en/hooks#sessionstart-input)) |
| PreModelSwitch | `context_tokens`, `estimated_cache_write_usd`, `pricing` | "Estimated cost in US dollars of writing `context_tokens` to the prompt cache on `to_model`" ([hooks#premodelswitch](https://code.claude.com/docs/en/hooks#premodelswitch)) |
| PostToolUse on `Agent` | `totalTokens`, `usage` | "Token count from the subagent's final API request ... This isn't a total across the whole run"; for rollups "use the token and cost counters filtered to `query_source` `"subagent"`" ([hooks, Agent tool](https://code.claude.com/docs/en/hooks#agent)) |
| Stop | none | gets "`stop_hook_active`, `last_assistant_message`, `background_tasks`, and `session_crons`" ([hooks#stop-input](https://code.claude.com/docs/en/hooks#stop-input)) |
| SessionEnd | none | gets "a `reason` field"; default budget "1.5 seconds" ([hooks#sessionend-input](https://code.claude.com/docs/en/hooks#sessionend-input)) |

The two `estimated_cache_write_usd` fields are forecasts of a future cache write, not spend so far. No hook reports the session's running cost.

### 3.2 Status line JSON

| Field | Docs quote |
| :- | :- |
| `cost.total_cost_usd` | "Estimated session cost in USD, computed client-side at list price unless a `modelPricing` table is in effect. May differ from your actual bill. Resets to $0 when `/clear` starts a new session" |
| `session_id` | "Unique session identifier" |
| `transcript_path` | "Path to conversation transcript file" |
| `cwd`, `workspace.current_dir` | "Current working directory" |
| `workspace.git_worktree` | "Git worktree name when the current directory is inside a linked worktree created with `git worktree add`" |
| `worktree.branch` | present "only while the session is in a worktree session" (Claude Code's own `--worktree` sessions, not tower's) |
| `context_window.total_input_tokens` | "Token counts currently in the context window, from the most recent API response" — not a session total |

All from [statusline#available-data](https://code.claude.com/docs/en/statusline#available-data).

- `cost.total_cost_usd` "accumulates the estimated cost of all API calls in the current session". ([statusline#cost-and-duration-tracking](https://code.claude.com/docs/en/statusline#cost-and-duration-tracking)) The same figure as `/usage`: "The same total appears in the status line's cost field". ([costs#using-the-usage-command](https://code.claude.com/docs/en/costs#using-the-usage-command))
- It runs only on triggers: "once when a session starts", then on "A new assistant message arrives", `/compact`, mode changes, a `refreshInterval` timer, etc. "If a new update triggers while your script is still running, Claude Code cancels the in-flight script." ([statusline#when-the-status-line-updates](https://code.claude.com/docs/en/statusline#when-the-status-line-updates))
- Costs to tower: the user has one `statusLine` setting, so tower would have to own it or wrap the user's command; the last value before exit is the session's cost only if the last assistant message triggered a run that wasn't cancelled. Headless `-p` runs were not documented as running the status line.

### 3.3 Transcript JSONL

Docs:

- Location: "`~/.claude/projects/<project>/<session-id>.jsonl`, where `<project>` is your working directory path with non-alphanumeric characters replaced by `-`". ([sessions#where-transcripts-are-stored](https://code.claude.com/docs/en/sessions#where-transcripts-are-stored))
- Stability: "Each line is a JSON object: a message, a tool use or result, a record of a request Claude Code sent the model, or a metadata entry. The entry format is internal to Claude Code and changes between versions, so scripts that parse these files directly can break on any release." (same)
- Retention: "Change the 30-day retention | `cleanupPeriodDays`". Transcripts older than 30 days are deleted by default, so tower must read them before then. (same)
- The OTel page confirms the join key exists: "`request_id` on the API events, persisted as `requestId` on the transcript's assistant entries", while warning to "treat the joins as version-specific rather than a stable contract". ([monitoring-usage#event-correlation-attributes](https://code.claude.com/docs/en/monitoring-usage#event-correlation-attributes))
- The docs do **not** document a `message.usage` field.

Local observation (not docs; one transcript from this repo, Claude Code `2.1.304`):

- Assistant entries have `message.usage` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, and `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`. Also `message.model`.
- The same entries carry top-level `sessionId`, `cwd`, `gitBranch`, `requestId`, `timestamp`, `isSidechain`. So one file gives session, branch and tokens together, with no join to tower's log needed.
- **Duplicates**: 26 usage-bearing lines but only 15 distinct (`requestId`, `message.id`) pairs. One API response is split across several lines with the same usage. Summing without dedupe over-counts.
- **Subagents** write to `<session-id>/subagents/agent-<id>.jsonl` next to the main file. A total that reads only the main file misses subagent spend.

### 3.4 Other paths, rejected

- `claude -p --output-format json` returns "the result, session ID, usage, and cost of a non-interactive run". Only for headless runs tower starts itself. ([sessions#access-conversations-from-scripts](https://code.claude.com/docs/en/sessions#access-conversations-from-scripts))
- `/usage` shows the same session total, but only interactively. ([costs#using-the-usage-command](https://code.claude.com/docs/en/costs#using-the-usage-command))

## 4. Pricing caveat for any computed figure

- Claude Code's own figures are list-price estimates: "Claude Code computes the dollar figure locally from token counts at list price, unless a `modelPricing` table is in effect." ([costs#using-the-usage-command](https://code.claude.com/docs/en/costs#using-the-usage-command))
- `modelPricing` is managed-settings only: "Claude Code ignores it in user, project, and local settings". ([settings-reference#modelpricing](https://code.claude.com/docs/en/settings-reference#modelpricing))
- Subscriptions: "Claude Pro, Max, and Ultra subscribers have usage included in their subscription, so the session cost figure isn't relevant for billing purposes." ([costs#using-the-usage-command](https://code.claude.com/docs/en/costs#using-the-usage-command)) On a subscription, tokens are the honest metric and dollars are a notional "API-equivalent" number.
- Deriving cost from transcript tokens means tower keeps its own per-model price table (input, output, cache read, 5-minute and 1-hour cache write), which drifts as models and prices change. The data-residency 1.1x multiplier and fast-mode pricing are further edge cases ([costs#using-the-usage-command](https://code.claude.com/docs/en/costs#using-the-usage-command)).

## 5. Bottom line for tower

Simplest reliable path today, standard library only, no collector:

1. tower's hook log already records `session_id`, `cwd` and `transcript_path` per session.
2. At `tower stats` time (or in the SessionEnd hook, if the 1.5 s budget allows; lazy is safer), read each session's transcript plus its `subagents/*.jsonl`, keep assistant entries with `message.usage`, dedupe by `requestId` (fall back to `message.id`), and sum tokens per type per model.
3. Report **tokens** as the primary metric. Report **estimated cost** from a small, dated price table in tower, labelled as a list-price estimate.
4. Attribute to a branch via the hook `cwd` (worktree to branch) or the transcript's own `gitBranch`.

Risks: the transcript format is officially internal (pin a parser test to a fixture, fail soft on unknown shapes), 30-day transcript cleanup (read before then, or have SessionEnd copy the summed totals into tower's log), and price-table drift.

If tower later wants Claude Code's own cost number rather than its own estimate, it needs either a status line wrapper that records `cost.total_cost_usd` per `session_id`, or an OTLP receiver collecting `claude_code.api_request` events; both require changing the user's global settings, and the receiver must be running for every session.
