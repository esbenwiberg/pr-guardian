# Yolo — Permission-Free Task Execution

Execute the task below by spawning a `claude -p` subprocess with `--dangerously-skip-permissions`. The subprocess runs in the same working directory but has NO access to this conversation — you must bridge the context gap.

## Task

$ARGUMENTS

## Instructions

### 0. Validate

If the task above is empty or blank, ask the user what they want to run. Example: `/yolo commit with a good message` or `/yolo run tests and fix failures`. Do not proceed without a task.

### 1. Assess context needs

Classify the task:

- **Self-contained**: commit, test, lint, build, format, deploy, or anything where the subprocess can fully understand what to do by inspecting the filesystem. Needs minimal context — just a clear task description.
- **Context-dependent**: refactoring, implementing something discussed in this conversation, fixing a specific bug described verbally, or anything that references decisions/details only present in our conversation. Needs a context summary.

If the task is ambiguous or potentially destructive beyond what's stated (e.g., "clean up everything"), ask the user for clarification instead of proceeding.

### 2. Build the context file

Write a Markdown file to `/tmp/yolo-ctx-<unix-timestamp>.md` containing:

```markdown
# Task
<clear, actionable description of what to do>

# Working Directory
<current absolute path>

# Context                          ← only for context-dependent tasks
<summarized conversation decisions, constraints, and requirements>
<relevant file paths and their roles>
<key code snippets ONLY if the subprocess can't easily find them>

# Guidelines
<any constraints: don't modify X, keep backward compat, use Y pattern, etc.>

# Output
When you are done, you MUST end with a text summary of what you did. List files created, modified, or deleted, commands run, and any errors encountered. This is critical — if you only use tools without writing a text response, your output will be invisible.
```

Keep it focused. The subprocess is a capable Claude instance — it can read files, run commands, and figure things out. Only include what it genuinely cannot discover on its own.

### 3. Execute

Run the subprocess via a runner script. First create the runner at `/tmp/yolo-run.sh` (if it doesn't already exist), then invoke it.

**Runner script** (`/tmp/yolo-run.sh`):

```bash
#!/bin/bash
YOLO_CTX="$1"
YOLO_OUT="$2"

cat "$YOLO_CTX" | env -u CLAUDECODE claude -p \
  --dangerously-skip-permissions \
  --no-session-persistence \
  --append-system-prompt "After completing all tasks, you MUST write a final text response summarizing what you did: files changed, commands run, outcomes, and any errors. Without this text response your output is invisible." \
  > "$YOLO_OUT" 2>&1

rm -f "$YOLO_CTX"
```

**Invocation** (two sequential Bash calls):

1. First Bash call — write the runner script (if needed) and execute:
```bash
# write runner if missing
[ -f /tmp/yolo-run.sh ] || cat > /tmp/yolo-run.sh << 'RUNNER'
#!/bin/bash
cat "$1" | env -u CLAUDECODE claude -p \
  --dangerously-skip-permissions \
  --no-session-persistence \
  --append-system-prompt "After completing all tasks, you MUST write a final text response summarizing what you did: files changed, commands run, outcomes, and any errors. Without this text response your output is invisible." \
  > "$2" 2>&1
rm -f "$1"
RUNNER
chmod +x /tmp/yolo-run.sh

# run it
bash /tmp/yolo-run.sh "$YOLO_CTX" "/tmp/yolo-out.txt"
```

2. Second step — use the **Read** tool (NOT Bash cat) to read `/tmp/yolo-out.txt`. The parent session's Bash tool swallows stdout from nested `claude -p` processes, so you MUST use the Read tool to retrieve the output.

3. Clean up: `rm -f /tmp/yolo-out.txt`

### 4. Report

Read the subprocess output from the file using the Read tool, then show the user a concise summary:
- What actions were taken
- What files were changed (if any)
- Any errors or warnings
- The subprocess's own summary if useful

Do NOT just dump the raw output — summarize it. Clean up `/tmp/yolo-out.txt` after reading.
