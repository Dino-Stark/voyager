# Example Fixture Pattern: Source/Target Separation

## Problem

Voyager patch operations modify Java source files in the example projects. If the
same runtime directory is reused without cleanup, later tests start from a dirty
state and expected patch hunks may no longer match.

## Solution

Each example project has two directories:

- `examples/_sources/<project>/`: read-only gold-master source.
- `examples/<project>/`: runtime working copy used by Voyager.

Before each test, `examples/reset.py` deletes the runtime copy contents and
copies a fresh version from `_sources`.

## Directory Structure

```text
examples/
|-- _sources/            # gold-master source
|   |-- shop-dto/
|   |-- mini-customer/
|   `-- mini-order/
|-- shop-dto/            # runtime copy
|-- mini-customer/       # runtime copy
|-- mini-order/          # runtime copy
|-- reset.py
`-- README.md
```

## reset.py

```bash
# Reset one project
python examples/reset.py shop-dto

# Reset all projects
python examples/reset.py
```

The script:

1. Removes all files and subdirectories under the runtime target directory.
2. Copies all files from `examples/_sources/<project>/`.
3. Prints the reset result.

## Rules

- Never edit `_sources/` during manual testing.
- Treat runtime directories as disposable.
- Reset before example or manual verification.
- Reset after e2e runs if you want a clean workspace.

## Adding A New Example Project

1. Create `examples/_sources/<project_name>/`.
2. Put the gold-master source files there.
3. Run `python examples/reset.py <project_name>` to create the runtime copy.
4. Add focused coverage in `examples/e2e_v1.py` if the project exists to test a new behavior.
