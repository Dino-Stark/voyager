# Voyager V1 Architecture

## Scope

Voyager V1 solves one narrow problem:

> Safely rename a Java DTO/POJO field and keep all related source files consistent.

The LSP-backed rename operation is:

```json
{
  "op": "rename_field",
  "target": "com.shop.UserDTO.userName",
  "to": "customerName"
}
```

V1 also supports conservative static source edits for fields:

```json
{
  "op": "add_field",
  "target": "com.shop.OrderDTO",
  "field_name": "giftMessage",
  "field_type": "String"
}
```

```json
{
  "op": "remove_field",
  "target": "com.shop.OrderDTO",
  "field_name": "giftMessage"
}
```

`add_field` adds a private field plus JavaBean getter/setter methods. `remove_field`
removes the field and its conventional JavaBean accessor methods, but rejects the
operation when the field or accessors have external typed references.

V1 also supports agent-friendly unified diff patches:

```json
{
  "op": "patch",
  "patch": "--- a/src/main/java/com/shop/OrderDTO.java\n+++ b/src/main/java/com/shop/OrderDTO.java\n@@ ...\n"
}
```

`patch` is intended for CLI-first coding-agent workflows. It parses a unified
diff, validates that all target paths stay inside the project, applies hunks in
memory, supports file creation/deletion, rebuilds the graph, and commits
atomically through the same engine path.

---

## Design Principles

1. Semantic-first: operations target symbols, not raw text.
2. Correctness over cleverness: when uncertain, reject the operation.
3. All-or-nothing writes: partial success is not allowed.
4. Verifiable changes: rebuild graph and run post-validation before commit.
5. Long-lived project context: JDT LS belongs to a project Server, not to each short CLI command.

---

## Current Runtime Architecture

```text
CLI / future IDE / future Agent
        |
        | VoyagerServerClient
        v
Voyager Server
        |
        | owns
        v
ProjectSession
  - LspClient(JAVA)
  - ExecutionEngine
  - StorageManager

        |
        | LSP JSON-RPC over stdio
        v
JDT LS
```

The CLI is now a client. It no longer owns the execution pipeline directly. `voyager start` explicitly starts the project-scoped Server in the background, and `scan/plan/apply` still auto-start it on demand for convenience. The Server owns the long-lived `ProjectSession`, which keeps JDT LS warm across `scan -> plan -> apply`.

See [Voyager Server Mode.md](./Voyager%20Server%20Mode.md) for the detailed Server lifecycle.

---

## Source Layout

```text
src/
├── voyager_cmd/
│   ├── main.py          # click CLI: start/serve/scan/plan/apply/status/stop
│   ├── server.py        # python -m voyager_cmd.server
│   └── daemon.py        # legacy compatibility entrypoint
├── cli/commands/
│   ├── scan.py          # CLI presentation + server client call
│   ├── plan.py
│   └── apply.py
├── core/
│   ├── server/          # VoyagerServer, VoyagerServerClient, local protocol
│   ├── session/         # ProjectSession and legacy daemon aliases
│   ├── parser/          # Java parser: LSP first, static fallback
│   ├── graph/           # SemanticGraph and GraphBuilder
│   ├── operation/       # Pydantic operation/result models
│   ├── engine/          # planning/apply pipeline
│   ├── lsp/             # LSP client and language config
│   ├── rules/           # pre/post validators
│   └── diff/            # currently minimal utility layer
├── storage/             # .voyager persistence
└── utils/               # async helper
```

---

## Persistent Project State

All project-local derived state lives under the Java project root:

```text
.voyager/
├── graph.json              # semantic graph
├── pending_plan.json       # operation saved by plan for a later apply
├── operations.log          # successful apply history
├── rules.yaml              # optional rules
└── cache/
    ├── server.json         # connection info for the running Voyager Server
    └── server.log          # server/JDT LS lifecycle logs
```

JDT LS workspace data does not live inside the scanned project. `LspClient` stores it under the user cache directory, keyed by project path, to avoid JDT LS indexing its own workspace or `.voyager` state.

One Java project root maps to one Voyager Server process. Multiple terminals or IDE conversations inside the same project reuse that Server via `.voyager/cache/server.json`. Different project roots run independent Server processes, with isolated JDT LS workspaces, graphs, pending plans, and operation logs.

---

## Scan

```text
voyager scan .
  -> VoyagerServerClient.scan()
  -> project/scan
  -> ProjectSession.scan()
  -> parse_java_project_async(project_path, lsp_client=reused_jdtls)
  -> GraphBuilder.build(classes)
  -> StorageManager.save_graph()
```

Parser strategy:

- If JDT LS is available, try LSP `textDocument/documentSymbol`.
- Run the static parser as a completeness check.
- If LSP output is incomplete or fails, fall back to static parsing.

This keeps `scan` useful even when JDT LS is not fully ready, while still using LSP when it gives complete semantic facts.

---

## Plan

```text
voyager plan rename_field com.shop.UserDTO.userName customerName
voyager plan patch agent.patch
  -> build Operation model
  -> VoyagerServerClient.plan(operation)
  -> operation/plan
  -> ExecutionEngine.plan_async()
  -> RuleValidator.validate_pre()
  -> compute affected files
  -> save .voyager/pending_plan.json
```

`plan` does not write source files. It validates the operation and estimates the affected files.

For `rename_field`, affected-file calculation includes:

- the field declaration file,
- direct typed field-access references,
- JavaBean accessor methods (`getX`, `setX`, `isX`),
- typed method-call references to those accessors.

This makes `plan` align better with what JDT LS will actually modify during rename.

For `patch`, affected-file calculation comes from the unified diff file headers.

---

## Apply

```text
voyager apply -y
  -> read .voyager/pending_plan.json
  -> VoyagerServerClient.apply(operation)
  -> operation/apply
  -> ExecutionEngine.apply_async()
  -> validate_pre
  -> LSP prepareRename + rename
  -> apply returned edits in memory
  -> rebuild graph from in-memory modified files
  -> validate_post
  -> commit all patches
  -> save graph and operation log
```

`rename_field`, `rename_method`, and `rename_class` must use JDT LS
`textDocument/rename`. Voyager deliberately does not fall back to string
replacement for rename apply.

`add_field` and `remove_field` do not require JDT LS for apply. They are static
single-class source edits that still use the same pre-validation, in-memory graph
rebuild, post-validation, atomic commit, graph persistence, and operation log
pipeline as rename operations.

`patch` also does not require JDT LS for patch construction. It is a structured
operation around unified diff text, designed for coding agents that naturally
produce patches from CLI workflows.

---

## Rules And Validation

Pre-validation checks:

- target field exists,
- new field name does not conflict,
- custom rules do not block the operation.

Post-validation checks:

- new field exists after modification,
- old field definition is gone,
- old typed field access does not remain,
- duplicate DTO rules still pass.

If post-validation fails, patches are discarded before any file is written.

---

## Failure Model

Failure handling is conservative:

- pre-validation failure: return invalid result, touch no files;
- LSP failure: return error, touch no files;
- post-validation failure: discard in-memory patches, touch no files;
- commit failure: roll back files already written from in-memory backups.

The graph on disk is updated only after successful commit.

---

## V1 Non-Goals

V1 intentionally does not implement:

- full call graph,
- unsafe field removal when external references still exist,
- multi-language support,
- reflection or dynamic proxy analysis,
- Lombok generated-code analysis,
- Spring dependency injection analysis,
- automatic architecture design,
- multi-agent planning.

These can be future features, but V1 stays narrow so the rename pipeline remains testable and reliable.

---

## Future Direction

Voyager should avoid growing into a thick PSI-like editing layer. Coding agents
prefer CLI-first patch workflows, and reimplementing a lightweight IDE editing
engine would add complexity without matching how agents naturally modify code.

The preferred roadmap is:

1. Strengthen `patch` as the primary agent-facing edit operation.
2. Add explicit `add_file`, `remove_file`, and `move_file` operations.
3. Improve semantic graph query capabilities so agents can inspect symbols,
   references, and affected files before producing patches.
4. Keep LSP-backed rename operations as high-value semantic exceptions.
5. Pause expansion of complex PSI-like edit operations, such as broad automatic
   field removal or method-body rewrites.

In this model, Voyager helps agents see code structure, constrain risky edits,
validate patches, commit atomically, rebuild the graph, and roll back safely.
Agents remain responsible for producing the actual code changes.
