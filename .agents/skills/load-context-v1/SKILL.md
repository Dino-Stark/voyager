---
name: load-context-v1
description: Initializes Codex's understanding of the Voyager project by reading the current V1 design documents, source code, tests, examples, and project working rules, then summarizing architecture, design-vs-implementation gaps, verification expectations, documentation expectations, and current/next-step status. Use on first conversation in this repository, when the user asks for a project overview, architecture explanation, design document context, implementation status, roadmap suggestions, or asks Codex to resume Voyager work in a new conversation.
---

# Load Context V1

Initialize Codex's understanding of the Voyager project architecture and working agreements by loading the design docs and nearby implementation context before giving a project-level answer.

## Scope

This skill is read-only. Do not edit files while executing this skill unless the user separately asks for code changes.

When the user separately asks for code changes after this skill has loaded context, treat the project working rules below as active expectations for that work.

## Project Working Rules

After code changes in Voyager, completion means:

1. Unit tests pass.
   - Run the relevant focused tests while iterating.
   - Before final response, run the full unit suite with `python -m pytest -q`.

2. Example flows pass.
   - For rename-related work, run the relevant `examples/shop-dto` CLI flows from a fresh reset.
   - Reset examples before and after manual/example verification with `python examples/reset.py <project>`.
   - For current V1 rename coverage, verify `rename_field`, `rename_method`, and `rename_class` when affected.

3. Documentation is synchronized.
   - Update `designs/V1/` documents and `examples/README.md` when commands, behavior, manual steps, examples, or limitations change.
   - Keep manual test steps executable and aligned with current CLI syntax.
   - Current rename commands use fully qualified class names. Do not document old simple-name targets or deprecated unprefixed `voyager plan rename Class.field newName`.

4. Progress is explicit.
   - During work, keep a visible checklist/status: completed, in progress, and pending.
   - In the final response, summarize what was done, what was verified, what was not verified or is still pending, and practical next steps.
   - Distinguish required follow-up from optional next steps.

## Project Map

Key repository areas:

```text
voyager/
|-- designs/V1/          # V1 design documents, the foundation for implementation
|-- src/                 # Source code
|   |-- cli/             # Command-line interface
|   |-- core/            # Core engine
|   |   |-- diff/        # Diff engine
|   |   |-- engine/      # Execution engine
|   |   |-- graph/       # Semantic graph
|   |   |-- lsp/         # LSP client
|   |   |-- operation/   # Operation models
|   |   |-- parser/      # Parser
|   |   `-- rules/       # Rule validation
|   |-- storage/         # Storage management
|   |-- utils/           # Utilities
|   `-- voyager_cmd/     # Main entry point
|-- tests/               # Unit tests
|-- scripts/             # Pre/post scripts, such as JDTLS helpers
`-- examples/            # Example code, such as shop-dto
```

## Workflow

1. Read the V1 design documents in `designs/V1/`, prioritizing the current document set:
   - `Architecture V1.md`
   - `Project Structure and Reading Guide.md`
   - `Voyager Server Mode.md`
   - `Apply Pipeline.md`
   - `Manual Test Steps for Rename Field.md`

   Use these as supporting references when relevant:
   - `JDTLS Dependency Management.md`
   - `Example Fixture Pattern.md`

2. Scan the source tree and inspect the main implementation files when present:
   - `src/core/engine/execution_engine.py`
   - `src/core/graph/semantic_graph.py`
   - `src/core/lsp/client.py`
   - `src/core/parser/java_parser.py`
   - `src/core/diff/diff_engine.py`

3. Review `tests/test_static_v1.py` to understand current test coverage and expected behavior.
   Also review `tests/test_server_v1.py` when Server/client behavior or operation serialization is relevant.

4. Browse `examples/shop-dto/` to understand practical usage and sample inputs.
   For rename behavior, include the current `examples/shop-dto` fixture and `examples/README.md`.

5. Compare design intent with implementation:
   - Architecture consistency: which designed modules exist or are missing.
   - Interface matching: whether designed interfaces match actual code.
   - Missing features: design items not implemented yet.
   - Implementation deviations: meaningful differences from the V1 design.

6. Capture working status:
   - Already implemented.
   - Partially implemented or not yet implemented.
   - Verification gaps.
   - Documentation gaps.
   - Recommended next steps.

## Output

After loading context, respond with:

1. Project overview: one sentence describing Voyager.
2. Architecture highlights: core modules and responsibilities.
3. Design vs. implementation gaps: discrepancies found.
4. Working rules: summarize the post-change requirements for tests, examples, docs, and progress tracking.
5. Status: list what is done, what is not done, and what should be considered next steps.

Keep the answer concise, but include file references when they help the user navigate the codebase.
