# JDT LS Dependency And Lifecycle

## Role In Voyager V1

Voyager V1 uses Eclipse JDT Language Server for Java semantic operations.

JDT LS is required for:

- `rename_field` apply,
- `textDocument/prepareRename`,
- `textDocument/rename`,
- LSP-based `documentSymbol` scan when available.

JDT LS is not required for every V1 behavior:

- static parsing can still build a graph for simple projects,
- `plan` can run from the saved/static graph,
- but `apply rename_field` must fail safely if JDT LS is unavailable.

---

## Installation

Voyager expects `jdtls` to be discoverable on `PATH`.

Repository helper:

```bash
python -m scripts.setup_jdtls
python -m scripts.setup_jdtls --check
```

The helper downloads JDT LS into:

```text
scripts/jdtls/
```

and creates launcher scripts such as:

```text
scripts/jdtls.cmd
```

`scripts/jdtls/` is ignored by git because it contains downloaded binary/runtime files.

---

## Command Discovery

JDT LS command discovery lives in:

```text
src/core/lsp/config.py
```

For Java, the configured command is:

```python
command=["jdtls"]
```

On Windows, `LanguageConfig.find_server_command()` checks common executable suffixes:

- `.cmd`
- `.bat`
- `.exe`

If it finds `jdtls.cmd` or `jdtls.bat`, it wraps the command as:

```text
cmd.exe /c <resolved launcher>
```

---

## Runtime Lifecycle

JDT LS is no longer started once per CLI command. It is owned by the project-scoped Voyager Server.

```text
voyager scan .
  -> VoyagerServer starts
  -> ProjectSession.start()
  -> LspClient(Language.JAVA).start()
  -> JDT LS stays alive

voyager plan ...
  -> reuse same Server and JDT LS

voyager apply ...
  -> reuse same Server and JDT LS

voyager stop
  -> ProjectSession.close()
  -> LspClient.shutdown()
```

This avoids repeated JDT LS startup/shutdown noise and keeps the Java index warm between commands.

---

## JDT LS Workspace

JDT LS needs a workspace directory for indexes and metadata. Voyager does not put this workspace under the scanned Java project.

Current behavior:

```text
%LOCALAPPDATA%/Voyager/jdtls-workspaces/<project-hash>/
```

or the equivalent user cache location on non-Windows systems.

Reason:

- avoids JDT LS indexing `.voyager/` or its own workspace,
- avoids polluting the Java project,
- allows one stable workspace per project path.

---

## LSP Client Responsibilities

Main file:

```text
src/core/lsp/client.py
```

Responsibilities:

- start JDT LS as a subprocess,
- perform LSP initialize/initialized handshake,
- send JSON-RPC messages over stdio,
- read responses and diagnostics,
- expose semantic methods:
  - `get_symbols()`
  - `prepare_rename()`
  - `rename_symbol()`
- shut down the subprocess on `voyager stop`.

Server mode injects the long-lived `LspClient` into `ExecutionEngine`, so `apply_async()` does not create a fresh JDT LS process.

---

## Logging And Windows Encoding

Server logs go to:

```text
.voyager/cache/server.log
```

JDT LS stderr can contain characters that do not encode cleanly in the active Windows console code page. `LspClient` therefore decodes stderr with UTF-8 and common Windows fallbacks, and logs sanitized text.

Known JDT LS shutdown noise is filtered where possible, because JDT LS may emit diagnostics during platform shutdown even after the requested operation has completed successfully.

---

## Failure Policy

If JDT LS cannot be found:

- `scan` may still fall back to static parser,
- `plan` may still work from graph/static facts,
- `apply rename_field` returns `lsp_unavailable`,
- no string replacement fallback is attempted.

This is intentional: `rename_field` is a semantic operation and must be driven by a semantic engine.

---

## Related Files

| File | Purpose |
| --- | --- |
| `scripts/setup_jdtls.py` | optional helper to download/install JDT LS |
| `scripts/jdtls.cmd` | Windows launcher checked by PATH |
| `src/core/lsp/config.py` | command discovery and Java LSP settings |
| `src/core/lsp/client.py` | LSP subprocess and JSON-RPC client |
| `src/core/session/project_session.py` | owns the long-lived `LspClient` |
| `src/core/server/server.py` | owns `ProjectSession` lifetime |
