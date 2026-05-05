# Apply Pipeline

The `ExecutionEngine.apply()` method follows a fixed all-or-nothing pipeline:

```mermaid
flowchart TD
    START((apply)) --> LOAD["1. Load graph\nensure_graph"]
    LOAD --> PRE["2. Pre-validation\nRuleValidator.validate_pre"]

    PRE -->|violations| FAIL1["return ApplyResult\nsuccess=False"]
    PRE -->|valid| BUILD["3. Build patches\n_build_patches"]

    BUILD --> RENAME{Operation type?}
    RENAME -->|RenameField| LSP["Request LSP\nprepareRename → rename"]
    RENAME -->|AddField / RemoveField| UNSUPPORTED[UnsupportedOperationError]

    LSP --> EDITS["Convert LspWorkspaceEdit\n→ FilePatch"]
    EDITS -->|no patches| EMPTY["EngineError:\nno file changes"]
    EDITS -->|patches built| POST["4. Post-validation\nrebuild_graph_static"]

    POST --> POST_V["RuleValidator.validate_post"]
    POST_V -->|violations| FAIL2["return ApplyResult\nsuccess=False"]
    POST_V -->|valid| COMMIT["5. Commit\n_commit: write all patches"]

    COMMIT -->|write failure| ROLLBACK[Rollback from backups]
    COMMIT -->|success| PERSIST[6. Persist]
    PERSIST --> SAVE["Save graph to .voyager/graph.json\nAppend to operations.log"]
    SAVE --> OK(("return ApplyResult\nsuccess=True"))

    style FAIL1 fill:#f66,color:#fff
    style FAIL2 fill:#f66,color:#fff
    style UNSUPPORTED fill:#f90,color:#fff
    style EMPTY fill:#f90,color:#fff
    style ROLLBACK fill:#f90,color:#fff
    style OK fill:#6c6,color:#fff
```

## Error handling

- **Pre-validation failure**: short-circuits immediately, no files touched.
- **Post-validation failure**: patches are discarded (in-memory only), no files touched.
- **Write failure during commit**: all already-written files are rolled back from backups.
- **Unexpected exception**: treated as `INTERNAL_ERROR`, rolled back if possible.

## Key invariants

1. Either all patches are applied or none are (atomicity).
2. The graph on disk always matches the source code state.
3. `add_field` and `remove_field` are declared in models but not supported by the engine in V1.
