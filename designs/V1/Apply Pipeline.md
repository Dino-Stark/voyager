# Apply Pipeline

This document describes the current V1 apply path. Rename operations use JDT LS;
`add_field`, `remove_field`, and `patch` use conservative static source patches.
In normal CLI usage, apply runs inside the project-scoped Voyager Server:

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
   rename_field / rename_method / rename_class:
     - resolve target field from SemanticGraph
     - call JDT LS prepareRename
     - call JDT LS rename
     - convert LspWorkspaceEdit to FilePatch objects
   add_field:
     - resolve target class from SemanticGraph
     - insert a private field and JavaBean getter/setter
   remove_field:
     - resolve target field from SemanticGraph
     - remove the field and conventional JavaBean getter/setter methods
   patch:
     - parse unified diff text
     - validate paths stay inside the project root
     - apply hunks in memory with exact context matching

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

## Static Field Edits

`add_field` and `remove_field` are single-class static edits in V1. They do not
need JDT LS because they do not rewrite references across files.

`add_field`:

- inserts `private <type> <name>;` after existing fields,
- appends JavaBean getter/setter methods before the class closing brace,
- supports an optional single-expression default value.

`remove_field`:

- removes the field declaration,
- removes conventional JavaBean getter/setter methods for that field,
- rejects the plan if typed external field or accessor references are known.

Both operations still rebuild the graph in memory, run post-validation, and
commit atomically through the same engine path as rename operations.

## Unified Diff Patch

`patch` is an agent-friendly operation for CLI-first workflows:

```bash
voyager plan patch agent.patch
voyager apply -y
```

The operation stores unified diff text in `.voyager/pending_plan.json`. During
apply, Voyager parses the diff, rejects paths outside the project, applies hunks
against exact context, rebuilds the graph in memory, then commits atomically.
Unified diffs may modify existing files, create new files with `/dev/null` as
the old path, or delete files with `/dev/null` as the new path.

Patch construction does not require JDT LS. Patch validation is syntactic and
graph-level; Voyager does not attempt semantic intent recovery for arbitrary
diffs.

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
| target field/class not found | `ApplyResult(success=False)` |
| JDT LS not available | `lsp_unavailable`, no files touched |
| `prepareRename` rejects target | validation error, no files touched |
| JDT LS returns no edits | validation error, no files touched |
| `remove_field` has external typed references | plan rejected, no files touched |
| `patch` hunk context does not match | validation error, no files touched |
| `patch` path escapes project root | validation error, no files touched |
| post-validation finds stale old field | invalid result, no files touched |
| write failure | rollback attempted, error result |

---

## Current V1 Limits

- `add_field` and `remove_field` only edit the declaring class and do not chase dynamic usages.
- `patch` applies unified diffs exactly; it is not a semantic refactoring operation.
- Post-validation uses the static parser for speed and determinism.
- Setter parameter names may remain unchanged after JDT LS field rename. This is accepted in V1 because the parameter is a local variable, not the renamed field symbol.
