# Apply Pipeline

This document describes the current V1 `rename_field` apply path. In normal CLI usage, apply runs inside the project-scoped Voyager Server:

```text
voyager apply
  -> cli.commands.apply.apply_plan()
  -> VoyagerServerClient.apply(operation)
  -> VoyagerServer: operation/apply
  -> ProjectSession.apply()
  -> ExecutionEngine.apply_async()
```

`ExecutionEngine.apply()` still exists as a synchronous wrapper for programmatic use, but the Server path uses `apply_async()` so it can reuse the long-lived `LspClient`.

---

## Pipeline

```text
1. Load pending operation
   .voyager/pending_plan.json

2. Send operation to Server
   VoyagerServerClient.apply()

3. Pre-validate
   RuleValidator.validate_pre(graph, operation)

4. Build patches
   rename_field:
     - resolve target field from SemanticGraph
     - call JDT LS prepareRename
     - call JDT LS rename
     - convert LspWorkspaceEdit to FilePatch objects

5. Rebuild graph in memory
   parse modified file contents without writing them yet

6. Post-validate
   RuleValidator.validate_post(new_graph, operation)

7. Commit
   write all modified files
   rollback on write failure

8. Persist
   save .voyager/graph.json
   append .voyager/operations.log
   clear .voyager/pending_plan.json in CLI
```

---

## Why LSP Rename Is Required

`rename_field` can affect:

- field declarations,
- direct field usages,
- JavaBean getter/setter names,
- cross-file method calls,
- references known only to the Java language server.

Voyager therefore uses JDT LS `textDocument/rename` for the actual edit set. It does not use string replacement as a fallback, because that would break the semantic-first design principle.

---

## Atomicity

The engine builds all file patches in memory before writing anything.

```text
original files
  -> LSP workspace edit
  -> FilePatch(original, modified)
  -> post-validation
  -> commit
```

If validation fails, no source file is touched.

If writing fails partway through commit, already-written files are restored from their `FilePatch.original` content.

---

## Error Cases

| Case | Result |
| --- | --- |
| target field not found | `ApplyResult(success=False)` |
| JDT LS not available | `lsp_unavailable`, no files touched |
| `prepareRename` rejects target | validation error, no files touched |
| JDT LS returns no edits | validation error, no files touched |
| post-validation finds stale old field | invalid result, no files touched |
| write failure | rollback attempted, error result |

---

## Current V1 Limits

- `add_field` and `remove_field` are declared in operation models but rejected by `ExecutionEngine`.
- Post-validation uses the static parser for speed and determinism.
- Setter parameter names may remain unchanged after JDT LS field rename. This is accepted in V1 because the parameter is a local variable, not the renamed field symbol.
