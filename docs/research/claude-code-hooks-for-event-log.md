# Claude Code hooks for the event log (#22)

Research for #22 (event log from Claude Code hooks). Checked **2026-11-06** against the official Claude Code docs only (the Markdown builds of the pages, e.g. `https://code.claude.com/docs/en/hooks.md`). The docs change often; re-check before relying on version-gated details.

Sources:

- Hooks reference: https://code.claude.com/docs/en/hooks
- Plugin loading reference: https://code.claude.com/docs/en/plugins/loading
- Host and maintain a marketplace: https://code.claude.com/docs/en/plugins/host-marketplace
- Plugin troubleshooting: https://code.claude.com/docs/en/plugins/troubleshooting
- Plugin manifest reference: https://code.claude.com/docs/en/plugins/manifest-reference
- Plugin commands reference: https://code.claude.com/docs/en/plugins/cli-reference

(`https://code.claude.com/docs/en/plugins-reference` now serves the plugin manifest reference; the caching and symlink text lives on the `plugins/loading` and `plugins/host-marketplace` pages.)

## TL;DR

- **SessionStart and SessionEnd both exist.** SessionStart gets `source` (`startup|resume|clear|compact|fork`), SessionEnd gets `reason` (`clear|resume|logout|prompt_input_exit|other`). Both filter on those values with `matcher`.
- **Symlink `plugin/lib/tower -> ../../src/tower` works for tower's GitHub-marketplace install**: a symlink that points elsewhere *inside the same marketplace* is dereferenced, and the target's content is copied into the cache. A symlink that points outside the marketplace is skipped. Caveats below.
- **A logging hook must exit 0 and print nothing on stdout.** On SessionStart and UserPromptSubmit, plain-text stdout is added as context Claude sees. Exit 2 blocks UserPromptSubmit and Stop. Any other non-zero exit is a non-blocking error, but it shows a `hook error` notice.
- **Default timeout is 600 s for command hooks**, 30 s on UserPromptSubmit, and SessionEnd hooks share a 1.5 s budget.

## 1. SessionStart and SessionEnd

### SessionStart

- Exists, and only command and MCP tool hooks run on it: "SessionStart runs on every session, so keep these hooks fast. Only `type: "command"` and `type: "mcp_tool"` hooks are supported." ([hooks#sessionstart](https://code.claude.com/docs/en/hooks#sessionstart))
- Stdin fields: the common fields, plus "`source` and optionally `model`, `agent_type`, and `session_title`" ([hooks#sessionstart-input](https://code.claude.com/docs/en/hooks#sessionstart-input))

| Field | Docs quote |
| :- | :- |
| `source` | "`"startup"` for new sessions, `"resume"` for resumed sessions, `"clear"` after `/clear`, `"compact"` after compaction, or `"fork"` for a new session forked from an existing one" |
| `model` | "It can be omitted, for example after `/clear` or when a session is restored through conversation recovery, so check for the field before reading it" |
| `agent_type` | "present when you start Claude Code with `claude --agent <name>`" |
| `session_title` | "The session's custom title, present when one is set" |
| `seconds_since_last_response`, `context_tokens`, `prompt_cache_likely_expired`, `estimated_cache_write_usd` | only "When `source` is `"resume"` or `"fork"` and the transcript contains at least one response from Claude" (v2.1.251+) |

Docs example:

```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
  "cwd": "/Users/...",
  "hook_event_name": "SessionStart",
  "source": "resume",
  "model": "claude-opus-5",
  "seconds_since_last_response": 5400,
  "context_tokens": 182340,
  "prompt_cache_likely_expired": true,
  "estimated_cache_write_usd": 1.1396
}
```

- Matchers: `startup`, `resume`, `clear`, `compact`, `fork`. The matcher table says "`SessionStart` | how the session started | `startup`, `resume`, `clear`, `compact`, `fork`" ([hooks#matcher-patterns](https://code.claude.com/docs/en/hooks#matcher-patterns)). `resume` covers "`--resume`, `--continue`, or `/resume`", and `compact` covers "Auto or manual compaction".
- Only SessionStart carries `model`: "Only [`SessionStart`] hooks can receive a `model` field, and Claude Code doesn't always include it." ([hooks#common-input-fields](https://code.claude.com/docs/en/hooks#common-input-fields))
- Timing: "SessionStart hooks run in the background. You can type right away ... Claude's first response still waits for the hooks to finish" ([hooks#sessionstart](https://code.claude.com/docs/en/hooks#sessionstart))

### SessionEnd

- Exists: "Runs when a Claude Code session ends. Useful for cleanup tasks, logging session statistics, or saving session state. Supports matchers to filter by exit reason." ([hooks#sessionend](https://code.claude.com/docs/en/hooks#sessionend))
- Stdin fields: the common fields plus "a `reason` field indicating why the session ended" ([hooks#sessionend-input](https://code.claude.com/docs/en/hooks#sessionend-input))

| `reason` | Docs quote |
| :- | :- |
| `clear` | "Session cleared with `/clear` command" |
| `resume` | "Session switched via interactive `/resume`" |
| `logout` | "User logged out" |
| `prompt_input_exit` | "User exited while prompt input was visible" |
| `other` | "Other exit reasons" |
| `bypass_permissions_disabled` | "Removed in v2.1.234; Claude Code doesn't send it" |

Docs example:

```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
  "cwd": "/Users/...",
  "hook_event_name": "SessionEnd",
  "reason": "other"
}
```

- Matchers: "`SessionEnd` | why the session ended | `clear`, `resume`, `logout`, `prompt_input_exit`, `other`" ([hooks#matcher-patterns](https://code.claude.com/docs/en/hooks#matcher-patterns))
- It can't block and its output is dropped: "SessionEnd hooks have no decision control. They can't block session termination ... Claude Code discards their JSON output fields" ([hooks#sessionend-input](https://code.claude.com/docs/en/hooks#sessionend-input))
- **Tight timeout, and plugin hooks can't raise it:** "SessionEnd hooks have a default timeout of 1.5 seconds." and "Timeouts set on plugin-provided hooks don't raise the budget." The override is the `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` environment variable. (same section)
- Note: `/clear` and `/resume` fire SessionEnd (`reason` = `clear`/`resume`) and then SessionStart (`source` = `clear`/`resume`). One terminal session can therefore produce several session_ids. That pairing is an inference from the two tables, not a quote.

## 2. Common and per-event stdin fields

### Common fields ([hooks#common-input-fields](https://code.claude.com/docs/en/hooks#common-input-fields))

| Field | Docs quote / note |
| :- | :- |
| `session_id` | "Current session identifier" |
| `prompt_id` | "UUID identifying the user prompt currently being processed ... Absent until the first user input" |
| `transcript_path` | "Path to conversation JSON. The transcript file is written asynchronously and may lag" |
| `cwd` | "Current working directory when the hook is invoked" |
| `scratchpad_dir` | "Absent when the session has no scratchpad ... Requires Claude Code v2.1.257 or later" |
| `permission_mode` | "`"default"`, `"plan"`, `"acceptEdits"`, `"auto"`, `"dontAsk"`, or `"bypassPermissions"` ... Not all events receive this field" |
| `effort` | "Object whose `level` is the effort level ... Present for `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`, and other events with a tool-use context" |
| `hook_event_name` | "Name of the event that fired" |
| `agent_id`, `agent_type` | "`agent_id` and `agent_type` tell your script which agent a hook fired in, such as a subagent" |

For a robust parser, only `session_id`, `transcript_path`, `cwd`, and `hook_event_name` show up in every per-event example. Treat everything else as optional.

### Stop ([hooks#stop-input](https://code.claude.com/docs/en/hooks#stop-input))

- "Stop hooks receive `stop_hook_active`, `last_assistant_message`, `background_tasks`, and `session_crons`."
- `stop_hook_active`: "`true` when Claude Code is already continuing as a result of a stop hook."
- `last_assistant_message`: "contains the text content of Claude's final response"
- `background_tasks` / `session_crons`: "let hooks distinguish "session is done" from "session is paused waiting for background work"". Both are arrays, and they're empty when nothing is in flight.
- Fire rules: "Runs when the main Claude Code agent has finished responding. Does not run if the stoppage occurred due to a user interrupt. API errors fire [StopFailure] instead." ([hooks#stop](https://code.claude.com/docs/en/hooks#stop))

### Notification ([hooks#notification-input](https://code.claude.com/docs/en/hooks#notification-input))

- "Notification hooks receive `message` with the notification text, an optional `title`, and `notification_type` indicating which type fired."
- `notification_type` / matcher values: `permission_prompt`, `idle_prompt`, `auth_success`, `auth_storage_failure`, `elicitation_dialog`, `elicitation_url_dialog`, `elicitation_complete`, `elicitation_response`, `agent_needs_input`, `agent_completed`, `quota_auto_resume_fired`, `quota_auto_resume_stale`, `quota_auto_resume_disabled`, `plugin_notification` ([hooks#matcher-patterns](https://code.claude.com/docs/en/hooks#matcher-patterns))
- Timing caveat: "`permission_prompt`, `idle_prompt`, `elicitation_dialog`, and `elicitation_url_dialog` types share their timing with desktop notifications". A `permission_prompt` fires only after "you haven't typed for about six seconds", and `idle_prompt` "about 60 seconds after Claude finishes responding" ([hooks#notification](https://code.claude.com/docs/en/hooks#notification)). For an immediate "waiting on permission" event, the docs point to PermissionRequest instead.
- "Notification hooks can't block or modify notifications." (same section)

### UserPromptSubmit ([hooks#userpromptsubmit-input](https://code.claude.com/docs/en/hooks#userpromptsubmit-input))

- "UserPromptSubmit hooks receive the `prompt` field containing the submitted text." Also `session_title` when a custom title is set, and `permission_mode` appears in the example.
- It fires on more than typed prompts: also on "A session-scoped scheduled task firing, including a `/loop` iteration", "A background subagent reporting back", and "A message another session sends" ([hooks#userpromptsubmit](https://code.claude.com/docs/en/hooks#userpromptsubmit))
- For a skill: "the hook receives that line as you typed it rather than the expanded skill content"

### PostToolUse ([hooks#posttooluse-input](https://code.claude.com/docs/en/hooks#posttooluse-input))

- "The input includes both `tool_input`, the arguments sent to the tool, and `tool_response`, the result it returned. The exact schema for both depends on the tool."
- Also `tool_name`, `tool_use_id`, an optional `duration_ms` ("Tool execution time in milliseconds"), `permission_mode`, and for MCP tools "the `mcp_server` object".
- It fires only on success: "Runs immediately after a tool completes successfully." Failures go to PostToolUseFailure. ([hooks#posttooluse](https://code.claude.com/docs/en/hooks#posttooluse))
- Matcher: "Matches on tool name, same values as PreToolUse". Omit the matcher or use `"*"` for all tools.

### Matchers on events without matcher support

"`UserPromptSubmit`, `PostToolBatch`, `Stop`, ... | no matcher support | always fires on every occurrence" and "If you add a `matcher` field to an event without matcher support, it is silently ignored." ([hooks#matcher-patterns](https://code.claude.com/docs/en/hooks#matcher-patterns))

## 3. Plugin cache and symlinks (the `plugin/lib/tower -> ../../src/tower` question)

### What the docs say

The cache copy ([plugins/loading#in-place-and-copied-plugins](https://code.claude.com/docs/en/plugins/loading#in-place-and-copied-plugins)):

> **Every other marketplace plugin**: Claude Code copies the plugin into `cache/<marketplace>/<plugin>/<version>/` at install and loads that copy. Files outside the plugin directory aren't copied, so when a script inside a copied plugin reads a path above the plugin root, such as `../shared`, it doesn't find them

Symlinks during the copy ([plugins/host-marketplace#share-files-within-a-marketplace-with-symlinks](https://code.claude.com/docs/en/plugins/host-marketplace#share-files-within-a-marketplace-with-symlinks)):

> To share files between your plugin and other parts of the same marketplace, create symbolic links inside your plugin directory. When Claude Code copies the plugin into its cache, it handles each symlink by where the target resolves:
>
> * **Within the plugin's own directory**: the symlink is preserved as a relative symlink in the cache, so it keeps resolving to the copied target at runtime.
> * **Elsewhere within the same marketplace**: the symlink is dereferenced. The target's content is copied into the cache in its place. This lets a meta-plugin's `skills/` directory link to skills defined by other plugins in the marketplace.
> * **Outside the marketplace**: the symlink is skipped for security.
>
> For plugins installed from a local path, or from a [`command` source] whose `mode` is the default `copy`, Claude Code preserves only symlinks that resolve within the plugin's own directory and skips all others.

The troubleshooting page recommends symlinks for this case ([plugins/troubleshooting#files-the-plugin-references-outside-its-directory-arent-found](https://code.claude.com/docs/en/plugins/troubleshooting#files-the-plugin-references-outside-its-directory-arent-found)):

> Claude Code copies an installed plugin into its cache and loads it from there, so a path that reaches outside the plugin's own directory points at nothing in the cache. Move the shared files inside the plugin directory, or reference them through a symlink inside it.

Path traversal applies to *component* paths ([plugins/loading#paths-that-escape-the-plugin-directory](https://code.claude.com/docs/en/plugins/loading#paths-that-escape-the-plugin-directory)):

> Whether a plugin loads in place or from a cached copy, Claude Code doesn't let it declare components outside its own directory. It rejects a component path that resolves outside the plugin root ...
> * **A path that points outside the plugin as written**, such as `../shared-utils`
> * **A symlink that leads outside the plugin**, other than [links between plugins within one marketplace]

From the manifest reference ([plugins/manifest-reference](https://code.claude.com/docs/en/plugins/manifest-reference)): "**Containment**: a path that resolves outside the plugin root doesn't load ... `claude plugin validate` reports the error `Path contains ".." which could be a path traversal attempt`".

The validator doesn't follow symlinks ([plugins/cli-reference](https://code.claude.com/docs/en/plugins/cli-reference)): "Claude Code doesn't follow symlinks inside the directory you name." That rule covers linked `skills`/`agents`/`commands` directories and their entries.

### What that means for tower

tower's marketplace is the repo root (`.claude-plugin/marketplace.json`), the `tower` plugin's source is `./plugin`, and the README installs it with `claude plugin marketplace add rishi-desai/tower`, which is a Git-hosted marketplace. So `plugin/lib/tower -> ../../src/tower` resolves to `<repo>/src/tower`. That target sits outside the plugin but inside the marketplace, so:

- **GitHub marketplace install (the README path): works.** The symlink is dereferenced and `src/tower` is copied into `cache/tower/tower/<version>/lib/tower` as real files.
- **`--plugin-dir` or a marketplace added from a local path: works.** Per [plugins/loading](https://code.claude.com/docs/en/plugins/loading#in-place-and-copied-plugins), these load in place and nothing is copied, so the OS resolves the symlink normally.
- **Ambiguous case:** "plugins installed from a local path ... preserves only symlinks that resolve within the plugin's own directory and skips all others." If that applies to some local install path, `lib/tower` would be **missing** and the hook import would fail. Hooks should fail quietly (exit 0) if the import fails, so a broken install never disrupts a session.
- `lib/` is a plain directory, not a declared component (hooks/skills/agents/commands). So the component-path containment rule and the validator's no-follow rule don't apply to it.
- Versioning: `plugin/.claude-plugin/plugin.json` pins `"version": "0.7.1"`, and "a manifest that pins `"version": "1.0.0"` keeps every user on the cached copy until its author changes the string" ([plugins/loading#how-claude-code-computes-the-version](https://code.claude.com/docs/en/plugins/loading#how-claude-code-computes-the-version)). Changes to `src/tower` reach installed users only after a version bump, same as any plugin file.
- Don't write state into the plugin dir: "`${CLAUDE_PLUGIN_ROOT}`: the plugin's install directory. Each version has its own cache directory ... Don't write state there". Use `${CLAUDE_PLUGIN_DATA}` (`~/.claude/plugins/data/<id>/`, which survives updates) or tower's own ledger path. ([plugins/components](https://code.claude.com/docs/en/plugins/components))

## 4. Exit codes, stdout, and timeouts (keep a logging hook invisible)

From [hooks#exit-code-output](https://code.claude.com/docs/en/hooks#exit-code-output):

- Exit 0: "**Success**: your hook exits 0. Claude Code applies any JSON output fields your hook printed, and the action goes ahead"
- Exit 2: "**Blocking error**: your hook exits 2. On events that can block, Claude Code stops the action."
- Anything else: "**Non-blocking error**: your hook exits with any other code, or fails in some other way, such as not starting or printing invalid JSON. The action goes ahead" (unless the hook sets `onFailure: "block"`). The user still sees a "`<hook name> hook error` notice".

Stdout is added as context on two of our events ([hooks#exit-code-0](https://code.claude.com/docs/en/hooks#exit-code-0)):

> For most events, Claude Code writes stdout to the debug log and doesn't show it in the transcript. On these events, your stdout reaches Claude or the compaction summary:
> * **`UserPromptSubmit`, `UserPromptExpansion`, `SessionStart`, and `PostModelSwitch`**: Claude Code adds plain-text stdout as context that Claude can see and act on.

And: "Claude never sees stderr from a hook that exits 0."

Exit 2 per event, for the events we'd log ([hooks#exit-code-2-behavior-per-event](https://code.claude.com/docs/en/hooks#exit-code-2-behavior-per-event)):

| Event | Can block? | On exit 2 |
| :- | :- | :- |
| `UserPromptSubmit` | Yes | "Blocks the prompt, so it never reaches Claude" |
| `Stop` | Yes | "Prevents Claude from stopping, continues the conversation" |
| `PostToolUse` | No | "Shows stderr to Claude; the tool already ran" |
| `Notification` | No | "Exit code and stderr are ignored" |
| `SessionStart` | No | "Shows stderr to user only" |
| `SessionEnd` | No | "Shows stderr to user only" |

Timeouts ([hooks#common-fields](https://code.claude.com/docs/en/hooks#common-fields)):

> Defaults: 600 for `command`, `http`, and `mcp_tool`; 30 for `prompt`; 60 for `agent`. Claude Code lowers the `command`, `http`, and `mcp_tool` default to 30 on `UserPromptSubmit`, `PreModelSwitch`, and `PostModelSwitch`, and to 10 on `MessageDisplay`. `SessionEnd` hooks share a 1.5-second budget

When a hook times out, "Claude Code cancels a `command`, `http`, or `mcp_tool` hook that reaches its `timeout`, discarding the hook's output" ([hooks#timeouts](https://code.claude.com/docs/en/hooks#timeouts)). Hooks are synchronous by default: "By default, hooks block Claude's execution until they complete." ([hooks#run-hooks-in-the-background](https://code.claude.com/docs/en/hooks#run-hooks-in-the-background))

`async: true` is the opt-out: "Async hooks can't block or control Claude's behavior". The catch: "Claude Code delivers the `additionalContext` and `systemMessage` fields from the hook's JSON response to Claude on the next conversation turn", and in `-p` mode it "kills any async hook still running at teardown" ([hooks#how-async-hooks-execute](https://code.claude.com/docs/en/hooks#how-async-hooks-execute)).

### Rules for a logging-only hook (derived from the quotes above)

1. Always `exit 0`, including on internal errors. Never exit 2, because that blocks UserPromptSubmit and Stop. Avoid exit 1 too, since it shows a `hook error` notice to the user.
2. Print **nothing** to stdout. On SessionStart and UserPromptSubmit, plain text becomes context Claude sees. A stray `{...}` line can be parsed as JSON output.
3. Send diagnostics to stderr or a file. Claude doesn't see stderr on exit 0.
4. Keep it fast: an append to a local file. A sync hook stalls the session for as long as it runs, and SessionEnd has only 1.5 s. Plugin hooks can't raise that budget.
5. `async: true` is optional for a fast append. If used, still print nothing, so no JSON gets delivered next turn.
6. Don't set `onFailure: "block"`.
