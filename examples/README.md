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
```

Patch files can modify existing files, create files, delete files, and use
git-style rename metadata. Run one scenario at a time from a fresh reset so each
expected file list stays independent.

## E2E regression

Run the full example regression suite from the repository root:

```bash
python examples/e2e_v1.py
```

The script resets example projects, exercises ordered patch sets, file
create/modify/move/delete lifecycle patches, and the multi-project Server
isolation flow, then stops any Servers it started.
