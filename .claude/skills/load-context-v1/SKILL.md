---
name: "load-context-v1"
description: "Initializes Agent's understanding of project architecture by loading design docs and code context. Invoke on first conversation or when user needs project overview."
---

# Load Context V1

This skill initializes the Agent's understanding of the Voyager project architecture, loading relevant context for subsequent development tasks.

## When to Use This Skill

Use this skill when:

- This is the first conversation about the Voyager project
- User asks for a project overview or architecture explanation
- User wants to understand the codebase before starting development
- User asks about design documents or implementation details
- User wants to know the current development status vs. original design

## Project Structure Overview

```
voyager/
├── designs/V1/          # V1 design documents (foundation for code implementation)
├── src/                 # Source code
│   ├── cli/             # Command-line interface
│   ├── core/            # Core engine
│   │   ├── diff/        # Diff engine
│   │   ├── engine/      # Execution engine
│   │   ├── graph/       # Semantic graph
│   │   ├── lsp/         # LSP client
│   │   ├── operation/   # Operation models
│   │   ├── parser/      # Parser
│   │   └── rules/       # Rule validation
│   ├── storage/         # Storage management
│   ├── utils/           # Utilities
│   └── voyager_cmd/     # Main entry point
├── tests/               # Unit tests
├── scripts/             # Pre/post scripts (e.g., JDTLS)
└── examples/            # Example code (e.g., shop-dto)
```

## Execution Steps

### 1. Load Design Documents

Read all design documents in `designs/V1/`, focusing on:

- **Architecture V1.md** - Overall architecture design
- **LSP Architecture.md** - LSP implementation details
- **Design Decisions & Constraints.md** - Design decisions and constraints
- **Project Structure and Reading Guide.md** - Project structure guide

### 2. Load Core Source Code

Quickly scan the `src/` directory structure to understand module responsibilities:

- `core/engine/execution_engine.py` - Execution engine core
- `core/graph/semantic_graph.py` - Semantic graph building
- `core/lsp/client.py` - LSP client implementation
- `core/parser/java_parser.java_parser.py` - Java parser
- `core/diff/diff_engine.py` - Diff engine

### 3. Load Test Code

Review `tests/test_static_v1.py` to understand test coverage and patterns.

### 4. Load Example Code

Browse `examples/shop-dto/` to understand practical use cases and examples.

### 5. Compare Design Documents with Implementation

Analyze differences between design documents and actual code:

1. **Architecture Consistency** - Are all modules planned in design actually implemented?
2. **Interface Matching** - Do designed interfaces match actual implementations?
3. **Missing Features** - Features described in design but not yet implemented
4. **Implementation Deviations** - Differences between actual implementation and original design intent

## Output Format

After loading context, present to the user:

1. **Project Overview** - One-sentence description of what the project is
2. **Architecture Highlights** - Core modules and their responsibilities
3. **Design vs. Implementation Gaps** - Discrepancies found
4. **Development Roadmap Suggestions** - Recommended next steps based on gap analysis

## Notes

- This skill **reads files only, does not write**
- Focus on comparing design documents with code to provide valuable gap analysis
- Provide clear direction for subsequent development tasks
