# Voyager Examples

## Directory structure

```text
examples/
|-- _sources/            # Gold master copies; never modify these
|   `-- shop-dto/        # Source files for the shop-dto project
|-- shop-dto/            # Runtime copy; safe to modify and reset
`-- reset.py             # Reset script that copies from _sources
```

- **`_sources/`** contains the pristine, read-only source files. Never edit files here during testing.
- The runtime directories (e.g. `shop-dto/`) are working copies that Voyager operates on. After each test run, reset them.

## How to reset

```bash
# Reset a specific project
python examples/reset.py shop-dto

# Reset all projects
python examples/reset.py
```

This deletes all files in the runtime directory and copies fresh files from `_sources/`.

## shop-dto patch scenarios

The `shop-dto` fixture covers the V1 patch-only editing flow:

```bash
voyager plan patch agent.patch
voyager plan patch agent-1.patch agent-2.patch
git diff | voyager plan patch -
```

Patch inputs use Git-style unified diff. Patch files can modify existing files,
create files, delete files, and use git-style rename metadata. `voyager plan
patch -` reads one patch from stdin. When JDT LS is available for a
Maven/Gradle/Eclipse project, planned patches must leave the temporary snapshot
free of Java error diagnostics; partial symbol updates that only change a field
declaration but not its accessors or callers are rejected by diagnostics or
snapshot compile checks, and the CLI groups diagnostics by file when LSP details
are available.
Patch inputs and targets are UTF-8 text only; binary patches, symlink patches,
chmod/mode-only patches, and non-UTF-8 target files are rejected. Run one
scenario at a time from a fresh reset so each expected file list stays
independent.

Automation callers can use structured output:

```bash
voyager plan patch agent.patch --json
voyager apply -y --json
voyager status --json
voyager progress --json
```

The early Alita manual-patch bridge can also plan a supplied patch and record
Agent-layer artifacts:

```bash
voyager alita run "plan current changes" --patch agent.patch --json
git diff | voyager alita run "plan current changes" --patch - --json
```

It writes `.voyager/alita/runs/<run_id>/` with the context pack, patch attempt,
`tool-call-plan-patch-1.json`, plan result, and policy decision. It does not
call a model or apply files yet.

The CLI-first Alita tools expose the same patch boundary for agents:

```bash
voyager alita tool plan-patch --patch agent.patch --json
git diff | voyager alita tool plan-patch --patch - --json
voyager alita tool apply-patch --plan current --yes --json
voyager alita tool status --json
```

`plan-patch` saves a pending plan only after Voyager accepts the patch and
policy does not deny it. `apply-patch` replans the pending operation, evaluates
HITL policy, and applies through Voyager only after policy allows it or the user
approves. Without `--yes`, JSON mode returns `ask_user` instead of prompting.

The runtime-backed Agent entrypoint currently supports a deterministic manual
runtime and an optional ADK runtime:

```bash
voyager alita agent run "plan current changes" --runtime manual --patch agent.patch --json
git diff | voyager alita agent run "plan current changes" --runtime manual --patch - --json
voyager alita agent run "update DTO" --runtime adk --provider gemini --model <model> --json
```

The manual runtime treats `--patch` as the model-produced patch, records
`runtime-result.json` and `events.jsonl`, then asks Voyager to plan the patch.
The ADK runtime requires optional dependencies:

```bash
pip install -e .[adk]
```

## E2E regression

Run the full example regression suite from the repository root:

```bash
python examples/e2e_v1.py
```

The script resets example projects, exercises incomplete patch rejection through
LSP snapshot diagnostics, ordered patch sets, complete field/accessor/caller
patch updates, file create/modify/move/delete lifecycle patches, and the
multi-project Server isolation flow, then stops any Servers it started.
