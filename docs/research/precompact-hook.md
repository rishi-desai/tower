# PreCompact, PostCompact and SessionStart for worker memory (#93)

Research for #93 (worker memory survives compaction). Checked **2026-11-07** against the official Claude Code docs only (the Markdown builds of the pages, e.g. `https://code.claude.com/docs/en/hooks.md`). The docs change often; re-check before relying on version-gated details.

Sources:

- Hooks reference: https://code.claude.com/docs/en/hooks
- Hooks guide: https://code.claude.com/docs/en/hooks-guide
- Context window (what survives compaction): https://code.claude.com/docs/en/context-window
- Memory (CLAUDE.md): https://code.claude.com/docs/en/memory
- How Claude Code works: https://code.claude.com/docs/en/how-claude-code-works
- Manage costs: https://code.claude.com/docs/en/costs
- Commands: https://code.claude.com/docs/en/commands
- Settings reference: https://code.claude.com/docs/en/settings-reference

## TL;DR

- **PreCompact can't make the session's model do work.** It runs only `command`, `http` or `mcp_tool` hooks, not `prompt` or `agent` ones. It gets no turn with the model, so the model can't update a file by its own reasoning from this hook. The script can still read the transcript itself through `transcript_path`.
- **PreCompact's stdout steers the summary.** "When a PreCompact hook exits 0 and doesn't block, Claude Code uses its stdout as instructions for the compaction summary." It's capped at 10,000 characters per hook and cut at the cap with a marker, not saved to a file. Its `systemMessage` and `continue` fields are discarded. The docs list no `additionalContext` for PreCompact.
- **PreCompact can block.** Exit 2 or `"decision": "block"` stops compaction. A blocked proactive auto-compact is skipped and the conversation goes on. A blocked compaction that was recovering from a context-limit error makes the current request fail.
- **PreCompact input** is the common fields plus `trigger` (`manual`/`auto`, also the matcher values) and `custom_instructions`. That field holds the `/compact` argument, and it's `null` for auto or when the user passes nothing. `auto` also covers idle compaction.
- **PostCompact exists.** It gets `trigger` and `compact_summary` (the generated summary). It has no decision control and "can't affect the compaction result". The docs give it no way to add context. It's for logging or updating external state.
- **SessionStart with `source: "compact"` is the documented way to re-inject context.** Plain-text stdout and `hookSpecificOutput.additionalContext` both reach Claude. The context-window page says Claude Code "runs them and adds their output to the compacted context". Only `command` and `mcp_tool` hooks run on SessionStart.
- **SessionStart size cap: 10,000 characters per string.** That covers each `additionalContext` value, and plain stdout measured whole. Over the cap, Claude Code saves the text to a file in the session directory and gives Claude only "the file path and a preview of up to the first 2,000 characters". It "doesn't ask Claude to read the file". No setting raises the cap.
- **Other things that survive compaction**: project-root CLAUDE.md and auto memory are re-read from disk. The plan file is re-injected. Up to five recently modified files are re-read. Skill bodies come back, capped at 5,000 tokens each and 25,000 in total. A "Compact Instructions" section in CLAUDE.md, or `/compact <focus>`, steers the summary. Context that hooks added earlier is only summarized, not replayed.

## 1. PreCompact

### When it fires and matchers ([hooks#precompact](https://code.claude.com/docs/en/hooks#precompact))

"Runs before Claude Code is about to run a compact operation."

| Matcher | Docs quote |
| :- | :- |
| `manual` | "`/compact`" |
| `auto` | "Auto-compact when the conversation reaches the [auto-compact window], and [idle compaction]" |

The matcher table lists the same values: "`PreCompact`, `PostCompact` | what triggered compaction | `manual`, `auto`" ([hooks#matcher-patterns](https://code.claude.com/docs/en/hooks#matcher-patterns)). Idle compaction "compacts a long conversation that you've stepped away from" and is turned off with `"idleCompaction": false` ([settings-reference#idlecompaction](https://code.claude.com/docs/en/settings-reference#idlecompaction)).

### Input ([hooks#precompact-input](https://code.claude.com/docs/en/hooks#precompact-input))

> In addition to the common input fields, PreCompact hooks receive `trigger` and `custom_instructions`. For `manual`, `custom_instructions` contains what the user passes into `/compact` and is `null` when they pass nothing. For `auto`, `custom_instructions` is `null`.

```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
  "cwd": "/Users/...",
  "hook_event_name": "PreCompact",
  "trigger": "manual",
  "custom_instructions": null
}
```

`transcript_path` is a common field: "Path to conversation JSON. The transcript file is written asynchronously and may lag" ([hooks#common-input-fields](https://code.claude.com/docs/en/hooks#common-input-fields)). A PreCompact script can read the conversation itself. The lag caveat means the last few entries may be missing.

### Output: stdout becomes summary instructions ([hooks#precompact-output](https://code.claude.com/docs/en/hooks#precompact-output))

> When a PreCompact hook exits 0 and doesn't block, Claude Code uses its stdout as instructions for the compaction summary. Keep that stdout under 10,000 characters per hook. Claude Code cuts longer output at the cap and appends a marker.

The general exit-0 rules say the same ([hooks#exit-code-0](https://code.claude.com/docs/en/hooks#exit-code-0)):

> For most events, Claude Code writes stdout to the debug log and doesn't show it in the transcript. On these events, your stdout reaches Claude or the compaction summary:
> * **`UserPromptSubmit`, `UserPromptExpansion`, `SessionStart`, and `PostModelSwitch`**: Claude Code adds plain-text stdout as context that Claude can see and act on. ...
> * **`PreCompact`**: your stdout becomes instructions for the compaction summary

The size rule makes PreCompact the exception to the save-to-file behaviour ([hooks#json-output](https://code.claude.com/docs/en/hooks#json-output)): "**Over the limit**: except for PreCompact stdout, Claude Code saves the output to a file in the session directory."

Fields it discards ([hooks#precompact](https://code.claude.com/docs/en/hooks#precompact)): "Claude Code discards a PreCompact hook's `systemMessage` and `continue` fields."

What this means:

- PreCompact stdout doesn't reach the session's model as context. It reaches the summarization request, as instructions for what the summary keeps. Whatever the summary then contains is what the post-compaction model sees.
- The docs list no `hookSpecificOutput.additionalContext` for PreCompact. The decision-control table gives it only "Top-level `decision` | `decision: "block"`, `reason`" ([hooks#decision-control](https://code.claude.com/docs/en/hooks#decision-control)), and the "Add context for Claude" list of events doesn't include it ([hooks#add-context-for-claude](https://code.claude.com/docs/en/hooks#add-context-for-claude)).
- **Not documented:** whether the summarization request can use tools. The docs only say it "inherits your session's extended thinking configuration" ([context-window#what-survives-compaction](https://code.claude.com/docs/en/context-window#what-survives-compaction)). Nothing says the summarizer can write files.
- **Not documented:** whether a PreCompact hook that prints one JSON object (with no blocking decision) has that JSON used as summary instructions or parsed as JSON output. To be safe, print plain text that doesn't start with `{`.

### Blocking ([hooks#precompact](https://code.claude.com/docs/en/hooks#precompact))

> Exit with code 2 to block compaction. For a manual `/compact`, the stderr message is shown to the user. You can also block by returning JSON with `"decision": "block"`.
>
> Blocking automatic compaction has different effects depending on when it fires. If compaction was triggered proactively before the context limit, Claude Code skips it and the conversation continues uncompacted. If compaction was triggered to recover from a context-limit error already returned by the API, the underlying error surfaces and the current request fails.

Exit-2 table: "`PreCompact` | Yes | Blocks compaction" ([hooks#exit-code-2-behavior-per-event](https://code.claude.com/docs/en/hooks#exit-code-2-behavior-per-event)).

Blocking doesn't hand the model a turn. The docs describe no path from a PreCompact block to a message the model sees (`systemMessage` is discarded). So "block, let the model save its notes, then compact" isn't a documented flow.

### Hook types ([hooks#prompt-based-hooks](https://code.claude.com/docs/en/hooks#prompt-based-hooks))

PreCompact is in the list of "Events that support `command`, `http`, and `mcp_tool` hooks but not `prompt` or `agent`". PostCompact is in the same list.

Even where agent hooks are allowed, they're verifiers, not the session's model. An agent hook "spawns a subagent that can read files, search code, and inspect the codebase to verify conditions" and "returns a structured `{ "ok": true/false }` decision" ([hooks#agent-based-hooks](https://code.claude.com/docs/en/hooks#agent-based-hooks)). They're marked "experimental".

Default timeout: "600 for `command`, `http`, and `mcp_tool`" ([hooks#common-fields](https://code.claude.com/docs/en/hooks#common-fields)).

## 2. PostCompact

It exists ([hooks#postcompact](https://code.claude.com/docs/en/hooks#postcompact)):

> Runs after Claude Code completes a compact operation. Use this event to react to the new compacted state, for example to log the generated summary or update external state. Claude Code discards a PostCompact hook's `systemMessage` and `continue` fields.

Matchers are the same `manual` / `auto` ("After `/compact`" / "After auto-compact ... and after idle compaction").

Input ([hooks#postcompact-input](https://code.claude.com/docs/en/hooks#postcompact-input)): "PostCompact hooks receive `trigger` and `compact_summary`. The `compact_summary` field contains the conversation summary generated by the compact operation."

```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
  "cwd": "/Users/...",
  "hook_event_name": "PostCompact",
  "trigger": "manual",
  "compact_summary": "Summary of the compacted conversation..."
}
```

It can't add context: "PostCompact hooks have no decision control. They can't affect the compaction result but can perform follow-up tasks." The decision-control table puts it under "None | No decision control. Used for side effects like logging or cleanup" ([hooks#decision-control](https://code.claude.com/docs/en/hooks#decision-control)). Exit 2 "Shows stderr to user only" ([hooks#exit-code-2-behavior-per-event](https://code.claude.com/docs/en/hooks#exit-code-2-behavior-per-event)).

So PostCompact can save `compact_summary` to disk, but context for the model has to come from SessionStart.

## 3. SessionStart with `source: "compact"`

### It fires after compaction ([hooks#sessionstart](https://code.claude.com/docs/en/hooks#sessionstart))

Matcher "`compact` | Auto or manual compaction". The `source` field is "`"compact"` after compaction" ([hooks#sessionstart-input](https://code.claude.com/docs/en/hooks#sessionstart-input)). "Only `type: "command"` and `type: "mcp_tool"` hooks are supported." `mcp_tool` hooks are skipped at launch but run here: "When `SessionStart` fires again later in the session, after `/clear` or a compaction, its `mcp_tool` hooks run" ([hooks#events-that-fire-before-mcp-servers-are-available](https://code.claude.com/docs/en/hooks#events-that-fire-before-mcp-servers-are-available)).

### Its output becomes post-compaction context

- The guide recipe ([hooks-guide#re-inject-context-after-compaction](https://code.claude.com/docs/en/hooks-guide#re-inject-context-after-compaction)): "Use a `SessionStart` hook with a `compact` matcher to re-inject critical context after every compaction. Claude Code adds plain text your command writes to stdout to Claude's context."
- The compaction table ([context-window#what-survives-compaction](https://code.claude.com/docs/en/context-window#what-survives-compaction)): "SessionStart hooks that match the `compact` source | Claude Code runs them and adds their output to the compacted context"
- Both forms work ([hooks#sessionstart-decision-control](https://code.claude.com/docs/en/hooks#sessionstart-decision-control)): `additionalContext` is a "String added to Claude's context at the start of the conversation, before the first prompt", and "A hook that only adds context can print it without building JSON, because Claude Code adds a SessionStart hook's plain-text stdout to Claude's context."
- Delivery ([hooks#add-context-for-claude](https://code.claude.com/docs/en/hooks#add-context-for-claude)): "Claude Code wraps the string in a system reminder and inserts it into the conversation at the point where the hook fired." Write it as facts: "Text framed as out-of-band system commands can trigger Claude's prompt-injection defenses, which causes Claude to surface the text to you instead of treating it as context."
- Multiple hooks: "When several hooks return `additionalContext` for the same event, Claude receives all of the values."

### Size cap: 10,000 characters, then a file ([hooks#json-output](https://code.claude.com/docs/en/hooks#json-output))

> A hook's `additionalContext`, `systemMessage`, and `initialUserMessage` strings, and its plain stdout, are capped at 10,000 characters:
>
> * **Scope**: Claude Code measures each string on its own, even when several hooks run for the same event. For JSON output, each field is measured separately; plain stdout is measured whole.
> * **Over the limit**: except for PreCompact stdout, Claude Code saves the output to a file in the session directory. It replaces the output with the file path and a preview of up to the first 2,000 characters. ... Unlike that Bash ceiling, this cap has no setting or environment variable to raise it.
> * **Reading the file**: Claude Code doesn't ask Claude to read the file, so keep anything Claude must always see within the cap.

The same rule appears under [hooks#add-context-for-claude](https://code.claude.com/docs/en/hooks#add-context-for-claude): "If a value exceeds 10,000 characters, Claude Code writes the text to a file in the session directory and passes Claude the file path with a preview of up to the first 2,000 characters instead. Claude can read the file, but Claude Code doesn't ask it to."

So a SessionStart memory dump over 10,000 characters gets cut down to 2,000 characters of preview plus a path.

### Async SessionStart and compaction ([hooks#limitations](https://code.claude.com/docs/en/hooks#limitations))

"An async `SessionStart` hook runs every time the event fires. Claude Code skips the hook's output when the `additionalContext` and `systemMessage` text matches what Claude last received from that hook ... Claude Code delivers the text when it changes, and again the next time the hook runs after a compaction or `/clear`." Async output arrives "on the next conversation turn", so a synchronous hook is the safer choice for memory that must be there on the first post-compaction request.

## 4. Other ways instructions survive compaction

### What comes back by itself ([context-window#what-survives-compaction](https://code.claude.com/docs/en/context-window#what-survives-compaction))

| Mechanism | After compaction |
| :- | :- |
| System prompt and output style | "Both still apply" |
| Project-root CLAUDE.md and unscoped rules | "Re-injected from disk" |
| Auto memory | "Re-injected from disk" |
| Skill descriptions | "Rebuilt ... and re-injected. Requires Claude Code v2.1.304 or later" |
| The plan Claude wrote in plan mode | "Re-injected from disk" |
| Rules with `paths:` frontmatter, nested CLAUDE.md | "reloads them on demand" |
| Files Claude read or edited | "re-reads up to five, most recently modified first" ("A file over 5,000 tokens comes back as a path reference") |
| Invoked skill bodies | "Re-injected, capped at 5,000 tokens per skill and 25,000 tokens total; oldest dropped first" |
| Context that hooks added earlier | "Summarized with the rest of the conversation" |

The memory page agrees ([memory#instructions-seem-lost-after-compact](https://code.claude.com/docs/en/memory#instructions-seem-lost-after-compact)): "Project-root CLAUDE.md survives compaction: after `/compact`, Claude re-reads it from disk and re-injects it into the session." It adds: "If an instruction disappeared after compaction, it was given only in conversation ... Add conversation-only instructions to CLAUDE.md to make them persist."

The `InstructionsLoaded` hook reports these reloads: `load_reason` "`"compact"` ... fires when instruction files are re-loaded after a compaction event" ([hooks#instructionsloaded-input](https://code.claude.com/docs/en/hooks#instructionsloaded-input)). It's async and for observability only.

### Steering the summary

- "Compact Instructions" section in CLAUDE.md ([how-claude-code-works#when-context-fills-up](https://code.claude.com/docs/en/how-claude-code-works#when-context-fills-up)): "To control what's preserved during compaction, add a "Compact Instructions" section to CLAUDE.md or run `/compact` with a focus". The costs page gives the example heading `# Compact instructions` ([costs#manage-context-proactively](https://code.claude.com/docs/en/costs#manage-context-proactively)).
- `/compact [instructions]`: "Optionally pass focus instructions for the summary" ([commands](https://code.claude.com/docs/en/commands)). That text shows up as PreCompact's `custom_instructions`.
- The summary's default content ([how-claude-code-works#when-context-fills-up](https://code.claude.com/docs/en/how-claude-code-works#when-context-fills-up)): "The summary keeps your requests and key code snippets, but detailed instructions from early in the conversation may be lost."

## What this means for #93 (derived from the quotes above)

1. A hook can't ask the worker's model to write its memory file at compaction time. Every compaction hook is a script. The model's own notes have to be written during normal turns, through instructions in CLAUDE.md or a skill. Alternatively, a PreCompact or PostCompact script can extract them from `transcript_path` or `compact_summary`.
2. Use PreCompact stdout to tell the summarizer what to keep, such as "keep the ticket number, branch, current step, open decisions". Keep it under 10,000 characters or it gets cut.
3. Use a synchronous `SessionStart` `compact` command hook to re-inject the memory file. Keep the output under 10,000 characters. Over that, Claude only sees a 2,000-character preview and a path it isn't told to read.
4. Don't block auto-compaction to buy time. When compaction was recovering from a context-limit error, blocking fails the request.
5. Anything static belongs in project-root CLAUDE.md, which is re-read from disk after every compaction without a hook.
