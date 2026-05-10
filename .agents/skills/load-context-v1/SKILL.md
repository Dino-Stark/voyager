---
name: load-context-v1
description: Initializes Codex's understanding of the Voyager project by reading V1 design documents, source code, tests, and examples, then summarizing architecture and design-vs-implementation gaps. Use on first conversation in this repository, when the user asks for a project overview, architecture explanation, design document context, implementation status, or roadmap suggestions.
---

# Load Context V1

Initialize Codex's understanding of the Voyager project architecture by loading the design docs and nearby implementation context before giving a project-level answer.

## Scope

This skill is read-only. Do not edit files while executing this skill unless the user separately asks for code changes.

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

1. Read the V1 design documents in `designs/V1/`, prioritizing:
   - `Architecture V1.md`
   - `LSP Architecture.md`
   - `Design Decisions & Constraints.md`
   - `Project Structure and Reading Guide.md`

2. Scan the source tree and inspect the main implementation files when present:
   - `src/core/engine/execution_engine.py`
   - `src/core/graph/semantic_graph.py`
   - `src/core/lsp/client.py`
   - `src/core/parser/java_parser/java_parser.py`
   - `src/core/diff/diff_engine.py`

3. Review `tests/test_static_v1.py` to understand current test coverage and expected behavior.

4. Browse `examples/shop-dto/` to understand practical usage and sample inputs.

5. Compare design intent with implementation:
   - Architecture consistency: which designed modules exist or are missing.
   - Interface matching: whether designed interfaces match actual code.
   - Missing features: design items not implemented yet.
   - Implementation deviations: meaningful differences from the V1 design.

## Output

After loading context, respond with:

1. Project overview: one sentence describing Voyager.
2. Architecture highlights: core modules and responsibilities.
3. Design vs. implementation gaps: discrepancies found.
4. Roadmap suggestions: recommended next steps based on the gap analysis.

Keep the answer concise, but include file references when they help the user navigate the codebase.
