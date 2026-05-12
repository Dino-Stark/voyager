# Voyager

Voyager is a semantic code modification system for coding agents. V1 focuses on
one practical workflow: agents submit unified diff patch sets, Voyager validates
them against a Java project graph, and commits them atomically.

## V1 Goal

Make CLI-first code changes safer by combining:

- patch-only public edit API,
- virtual filesystem transactions,
- Java semantic graph rebuilds,
- optional JDT LS-backed snapshot validation,
- all-or-nothing commit and rollback.

## Requirements

| Dependency | Version | Notes |
| --- | --- | --- |
| Python | >= 3.10 | Runtime |
| JDK | 17+ | Required by JDT LS |
| JDT LS | current | Optional for static tests, recommended for semantic validation |

## Quick Start

Install in editable mode:

```bash
pip install -e .
```

Install or check JDT LS:

```bash
python -m scripts.setup_jdtls
python -m scripts.setup_jdtls --check
```

Run Voyager in a Java project:

```bash
cd /path/to/java/project
voyager start .
voyager scan .
voyager plan patch agent.patch
voyager apply -y
voyager stop
```

`scan/plan/apply` auto-start the project Server when needed, but explicit
`start` makes the lifecycle visible.

## Patch Workflow

Voyager accepts one or more patch files:

```bash
voyager plan patch agent-1.patch agent-2.patch
voyager apply -y
```

Patch files may:

- modify existing files,
- create files with `/dev/null` as the old path,
- delete files with `/dev/null` as the new path,
- move files with git-style `rename from` / `rename to` metadata,
- apply multiple ordered patches to the same virtual file.

## Core Principles

1. Patch-first: agents produce diffs with normal CLI/editor tools.
2. Semantic validation: Voyager rebuilds the Java graph after the virtual change.
3. Conservative failure: invalid patches touch no source files.
4. Atomic commit: partial writes are rolled back.
5. Project-scoped Server: one project root maps to one reusable Server and JDT LS lifecycle.

## Verification

```bash
python -m compileall -q src tests examples/e2e_v1.py
python -m pytest -q
python examples/e2e_v1.py
```

## Documentation

- [Architecture V1](designs/V1/Architecture%20V1.md)
- [Apply Pipeline](designs/V1/Apply%20Pipeline.md)
- [Voyager Server Mode](designs/V1/Voyager%20Server%20Mode.md)
- [Manual Test Steps](designs/V1/Manual%20Test%20Steps%20for%20Rename%20Field.md)
- [JDT LS Dependency Management](designs/V1/JDTLS%20Dependency%20Management.md)
