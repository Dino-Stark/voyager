# Voyager LSP 架构与实现详解

> 本文档深入解析 Voyager 项目中 LSP (Language Server Protocol) 的原理、用法，以及 rename symbol 的完整流程。适合新开发者阅读后结合代码继续开发。

---

## 一、LSP 概述

### 1.1 什么是 LSP

LSP (Language Server Protocol) 是微软提出的标准化协议，用于在编辑器/IDE 和语言分析服务之间进行通信。语言服务器独立运行，负责语法分析、跳转、重构等语义操作；编辑器通过 LSP 协议与其交互。

```
┌──────────────────────────────────────────────────────────┐
│  Editor / IDE (VS Code, Neovim, Cursor...)               │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐    │
│  │  UI Layer  │  │  LSP Client │  │  Buffer/State   │    │
│  └─────────────┘  └─────────────┘  └─────────────────┘    │
└──────────────────────────┬───────────────────────────────┘
                           │ JSON-RPC (stdio / TCP / WebSocket)
┌──────────────────────────▼───────────────────────────────┐
│  Language Server (jdtls, gopls, pyright...)              │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐    │
│  │   Parser   │  │    Index    │  │  Semantic       │    │
│  │   (AST)    │  │   (Index)   │  │  Analysis       │    │
│  └─────────────┘  └─────────────┘  └─────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

### 1.2 Voyager 中的 LSP 定位

Voyager 使用 LSP 作为**语义操作的来源**而非直接暴露给 Agent：

```
CLI / Agent
    ↓ (operation spec: rename_field X to Y)
ExecutionEngine
    ↓ (call LSP for semantic facts and edits)
LspClient
    ↓ (JSON-RPC over stdio)
jdtls (Eclipse JDT Language Server)
    ↓ (deep Java analysis)
WorkspaceEdit / SymbolInfo / Location
    ↓
ExecutionEngine validates and applies
```

这样做的好处：
- Agent 看到的是结构化的 operation，不是 LSP 协议
- 执行引擎保证事务性（all-or-nothing）
- LSP 故障时可以有 fallback 策略

---

## 二、LSP 协议基础

### 2.1 通信机制

Voyager 使用 **JSON-RPC over stdio**：

- 启动一个子进程（jdtls）
- 通过 stdin/stdout 进行 JSON-RPC 通信
- 每个消息前有 `Content-Length` header

```
Content-Length: 123\r\n
\r\n
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
```

**源码位置**: `src/core/lsp/client.py` 第408-414行

```python
async def _write_message(self, message: dict[str, Any]) -> None:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    self._process.stdin.write(header + body)
    await self._process.stdin.drain()
```

**消息读取** (`_read_message`, 第444-463行)：
1. 读取 header 行直到空行
2. 解析 `Content-Length` 值
3. 按该长度读取 body
4. JSON 反序列化

### 2.2 消息类型

| 类型 | 说明 | Voyager 用例 |
|------|------|-------------|
| Request | 有 id，需等待 response | `initialize`, `textDocument/rename` |
| Response | 对 Request 的回复 | 返回 `result` 或 `error` |
| Notification | 无 id，无需回复 | `initialized`, `textDocument/didOpen` |

### 2.3 核心数据结构

**源码位置**: `src/core/lsp/client.py` 第25-88行

```python
@dataclass(frozen=True)
class LspPosition:
    line: int      # 0-based
    character: int # 0-based, UTF-16 code unit

@dataclass(frozen=True)
class LspRange:
    start: LspPosition
    end: LspPosition

@dataclass(frozen=True)
class LspLocation:
    uri: str   # file:///... URI
    range: LspRange

@dataclass(frozen=True)
class LspTextEdit:
    range: LspRange
    new_text: str

@dataclass
class LspWorkspaceEdit:
    changes: dict[str, list[LspTextEdit]]  # uri → edits
```

### 2.4 URI 转换

LSP 使用 file URI，Voyager 提供了双向转换：

```python
# client.py 第91-108行
def path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()

def uri_to_path(uri: str) -> Path:
    # 处理 Windows 路径中的 / -> drive letter
    # file:///D:/project/... -> D:\project\...
```

---

## 三、LspClient 实现

### 3.1 类概述

**位置**: `src/core/lsp/client.py` 第111-543行

`LspClient` 是一个 async context manager，封装了：
- 进程生命周期（start / shutdown）
- 请求/通知发送
- 响应接收和路由
- 文件同步（didOpen / didChange）

### 3.2 生命周期

```
__aenter__() → start()
    ↓
LspClient 初始化完成，可使用 LSP 方法
    ↓
__aexit__() → shutdown()
```

**start()** (第141-208行) 流程：

```python
async def start(self) -> None:
    # 1. 找到服务器命令
    cmd = self.config.find_server_command()
    if cmd is None:
        raise RuntimeError("LSP server not found...")

    # 2. Java 特殊处理：生成独立 workspace 路径
    if self.language == Language.JAVA and "-data" not in cmd:
        workspace = self._jdtls_workspace_path()
        workspace.mkdir(parents=True, exist_ok=True)
        cmd = [*cmd, "-data", str(workspace)]

    # 3. 启动子进程
    self._process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(self.project_path),
        env=env,
    )
    self._reader_task = asyncio.create_task(self._read_loop())

    # 4. Initialize handshake
    await self._send_request("initialize", {...})
    # 5. 发送 initialized notification
    await self._send_notification("initialized", {})
    self._initialized = True
```

**JDT LS Workspace 隔离** (第238-248行)：

```python
def _jdtls_workspace_path(self) -> Path:
    # JDT LS 需要一个 workspace 目录存储索引
    # 放在用户缓存目录，避免和被扫描项目冲突
    digest = hashlib.sha1(str(self.project_path).encode("utf-8")).hexdigest()[:16]
    cache_root = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ...)
    return cache_root / "Voyager" / "jdtls-workspaces" / digest
```

**shutdown()** (第210-236行)：

```python
async def shutdown(self) -> None:
    await self._send_request("shutdown", None)  # 优雅关闭请求
    await self._send_notification("exit", None)  # 退出通知
    await asyncio.wait_for(process.wait(), timeout=5.0)
    # 失败则 kill 进程
```

### 3.3 异步请求模式

```
┌─────────────────┐
│ _send_request() │
│  1. id++        │
│  2. 创建 Future │
│  3. 存入 _pending[id] │
│  4. 发送 JSON-RPC │
│  5. await future│
└─────────────────┘
         ↓
┌─────────────────┐
│  _read_loop()  │ ← 在独立 Task 中运行
│  收到 response │
│  找到对应 Future│
│  set_result()  │
└─────────────────┘
```

### 3.4 文件同步

LSP 需要显式通知文件打开和修改：

| 方法 | 行号 | LSP 协议 |
|------|------|----------|
| `open_file()` | 350-368 | `textDocument/didOpen` |
| `change_file()` | 370-384 | `textDocument/didChange` |

```python
async def open_file(self, file_path: Path) -> None:
    text = file_path.read_text(encoding="utf-8")
    await self._send_notification("textDocument/didOpen", {
        "textDocument": {
            "uri": uri,
            "languageId": self.language.value,
            "version": 1,
            "text": text,
        }
    })
```

---

## 四、LSP 请求方法

### 4.1 方法总览

| 方法 | 行号 | LSP 协议 | 返回类型 |
|------|------|----------|----------|
| `get_symbols()` | 250-259 | `textDocument/documentSymbol` | `list[LspSymbolInfo]` |
| `get_references()` | 261-278 | `textDocument/references` | `list[LspLocation]` |
| `find_definitions()` | 280-294 | `textDocument/definition` | `list[LspLocation]` |
| `find_implementations()` | 296-310 | `textDocument/implementation` | `list[LspLocation]` |
| `prepare_rename()` | 312-326 | `textDocument/prepareRename` | `LspRange \| None` |
| `rename_symbol()` | 328-342 | `textDocument/rename` | `LspWorkspaceEdit` |
| `get_diagnostics()` | 344-348 | `textDocument/publishDiagnostics` (cache) | `list[dict]` |

### 4.2 get_symbols — 文档符号

```python
async def get_symbols(self, file_path: Path) -> list[LspSymbolInfo]:
    await self.open_file(file_path)
    result = await self._send_request(
        "textDocument/documentSymbol",
        {"textDocument": {"uri": uri}},
    )
    return self._parse_symbols(result or [], uri)
```

返回文档的符号树（类、方法、字段等），支持层级结构。

用于 `java_parser.py` 中的 `_analyze_with_lsp()`，将 LSP 符号转换为 `JavaClass` 对象。

### 4.3 get_references — 查找引用

```python
async def get_references(
    self,
    file_path: Path,
    position: LspPosition,
    include_declaration: bool = True,
) -> list[LspLocation]:
```

返回指定位置 symbol 的所有引用位置。

### 4.4 rename_symbol — 重命名

```python
async def rename_symbol(
    self, file_path: Path, position: LspPosition, new_name: str
) -> LspWorkspaceEdit:
    result = await self._send_request(
        "textDocument/rename",
        {
            "textDocument": {"uri": path_to_uri(file_path)},
            "position": position.to_lsp(),
            "newName": new_name,
        },
    )
    return self._parse_workspace_edit(result or {})
```

这是 `rename_field` 操作的核心，详见下一节。

---

## 五、Rename Symbol 原理

### 5.1 完整流程

```
用户命令: voyager plan rename_field OrderDTO.userId customerId
    ↓
CLI plan: 将参数转为 RenameFieldOp
    ↓
ExecutionEngine.plan(): 前置规则校验
    ↓
ExecutionEngine.apply(): 执行管线
    ├── validate_pre
    ├── _build_rename_patches
    │     ├── resolve field symbol (from graph)
    │     ├── 启动 LspClient
    │     ├── prepare_rename (确认可重命名)
    │     └── rename_symbol (获取所有 edits)
    ├── apply edits in memory
    ├── rebuild_graph_static (重新解析)
    ├── validate_post (后验校验)
    └── _commit (写文件)
```

### 5.2 源码解析

**入口**: `execution_engine.py` 第153-202行

```python
def _build_rename_patches(
    self, graph: SemanticGraph, operation: RenameFieldOp
) -> list[FilePatch]:
    # 1. 从语义图中找到目标字段
    field_symbol = graph.resolve_field(operation.class_name, operation.field_name)
    if field_symbol is None:
        raise SymbolNotFoundError(operation.target)

    # 2. 检查 jdtls 是否可用（核心依赖）
    if get_language_config(Language.JAVA).find_server_command() is None:
        raise LspUnavailableError(
            "rename_field requires jdtls on PATH..."
        )

    # 3. 调用 LSP rename（异步）
    workspace_edit = _run_async(
        self._request_lsp_rename(source_path, field_symbol, operation)
    )

    # 4. 转换为 FilePatch
    for uri, edits in workspace_edit.changes.items():
        path = uri_to_path(uri).resolve()
        original = path.read_text(encoding="utf-8")
        modified = apply_lsp_edits(original, edits)
        patches.append(FilePatch(path=path, original=original, modified=modified))

    return patches
```

**LSP 请求**: `execution_engine.py` 第204-223行

```python
async def _request_lsp_rename(
    self,
    source_path: Path,
    field_symbol: Any,
    operation: RenameFieldOp,
):
    async with LspClient(Language.JAVA, self.project_path) as client:
        # LSP 使用 0-based 位置，Java source 是 1-based
        position = LspPosition(
            line=field_symbol.line - 1,
            character=field_symbol.column - 1,
        )

        # Step 1: 预检（prepareRename）
        rename_range = await client.prepare_rename(source_path, position)
        if rename_range is None:
            raise EngineError("LSP rejected this location for rename")

        # Step 2: 执行重命名（rename）
        return await client.rename_symbol(source_path, position, operation.to)
```

**LSP 返回结构**:

```json
{
  "changes": {
    "file:///D:/project/src/OrderDTO.java": [
      {"range": {"start": {"line": 5, "character": 8}, "end": ...}, "newText": "customerId"}
    ],
    "file:///D:/project/src/OrderService.java": [
      {"range": {...}, "newText": "customerId"}
    ]
  }
}
```

### 5.3 Edit 应用算法

**位置**: `execution_engine.py` 第274-307行

LSP edits 可能重叠或交叉，必须按正确顺序应用：

```python
def apply_lsp_edits(content: str, edits: list[LspTextEdit]) -> str:
    # 1. 从后往前应用（保持前面的位置不变）
    ordered = sorted(
        edits,
        key=lambda edit: (offset_for(edit.range.start), offset_for(edit.range.end)),
        reverse=True,
    )

    result = content
    for edit in ordered:
        start = offset_for(edit.range.start)
        end = offset_for(edit.range.end)
        result = result[:start] + edit.new_text + result[end:]
    return result
```

**UTF-16 处理** (`_utf16_index_to_py_index`, 第318-324行)：

LSP 的 `character` 是 UTF-16 code unit（JavaScript 历史原因），Python 字符串是 UTF-8：

```python
def _utf16_index_to_py_index(text: str, utf16_index: int) -> int:
    units = 0
    for index, char in enumerate(text):
        if units >= utf16_index:
            return index
        units += len(char.encode("utf-16-le")) // 2
    return len(text)
```

### 5.4 为什么依赖 LSP 而非字符串替换

这是 Voyager 的核心设计原则：**语义优先**

| 方法 | 优点 | 缺点 |
|------|------|------|
| 字符串替换 | 无依赖，简单 | 无法处理重载、shadowing、反射、字符串中的同名文本 |
| LSP rename | 精确语义分析，跨文件，编译器级别保证 | 依赖 jdtls |

Voyager 的策略：
- `plan` / `scan` 可用静态 parser（无 LSP 依赖）
- `apply rename_field` **必须有 LSP**（不降级到字符串替换）
- 后验校验捕获 LSP 可能遗漏的角落情况

---

## 六、语言服务器配置

### 6.1 LanguageConfig

**位置**: `src/core/lsp/config.py`

```python
@dataclass
class LanguageConfig:
    language: Language
    file_extensions: list[str]
    command: list[str]                    # 服务器命令
    initialization_options: dict = ...   # 服务器特定配置
```

### 6.2 服务器发现

```python
def find_server_command(self) -> list[str] | None:
    # Windows 特殊处理：查找 .cmd, .bat, .exe
    if os.name == "nt":
        for suffix in (".cmd", ".bat", ".exe"):
            resolved = shutil.which(executable + suffix)
            if resolved:
                if suffix in {".cmd", ".bat"}:
                    return ["cmd.exe", "/c", resolved, *rest]
                return [resolved, *rest]
    # Unix: 查找 PATH 中的可执行文件
    return shutil.which(executable)
```

### 6.3 当前支持的服务器

| 语言 | 服务器 | 配置行号 | 状态 |
|------|--------|----------|------|
| Java | jdtls | 71-84 | ✅ 已实现 |
| Python | pyright-langserver | 85-91 | TODO |
| TypeScript | typescript-language-server | 92-98 | TODO |
| C# | OmniSharp | 99-105 | TODO |
| Go | gopls | 106-112 | TODO |
| C/C++ | clangd | 113-119 | TODO |

---

## 七、LSP 在 Java 解析中的应用

### 7.1 解析策略

**位置**: `java_parser.py` 第105-125行

```python
def parse_java_project(project_path: Path, prefer_lsp: bool = True) -> list[JavaClass]:
    if prefer_lsp and get_language_config(Language.JAVA).find_server_command():
        try:
            # 优先尝试 LSP
            classes = _run_async(_analyze_with_lsp(project_path))
            static_classes = parse_java_project_static(project_path)
            # 比较两者完整性
            if _is_lsp_result_complete_enough(classes, static_classes):
                return classes
            # LSP 不完整则 fallback 到静态解析
            return static_classes
        except Exception as exc:
            logger.warning("LSP failed, falling back to static: %s", exc)
    return parse_java_project_static(project_path)
```

### 7.2 完整性检查

```python
def _is_lsp_result_complete_enough(
    lsp_classes: list[JavaClass], static_classes: list[JavaClass]
) -> bool:
    # 1. LSP 必须有结果
    if not lsp_classes:
        return False
    # 2. FQN 集合必须一致
    static_by_fqn = {cls.fqn: cls for cls in static_classes}
    if static_by_fqn and {cls.fqn for cls in lsp_classes} != set(static_by_fqn):
        return False
    # 3. 成员数量不能更少
    lsp_members = sum(len(cls.fields) + len(cls.methods) for cls in lsp_classes)
    static_members = sum(len(cls.fields) + len(cls.methods) for cls in static_classes)
    return lsp_members >= static_members
```

### 7.3 LSP 符号转 JavaClass

```python
async def _analyze_with_lsp(project_path: Path) -> list[JavaClass]:
    async with LspClient(Language.JAVA, project_path) as client:
        java_files = [...]  # 所有 .java 文件
        for file_path in java_files:
            symbols = await client.get_symbols(file_path)
            for symbol in symbols:
                if _is_type_symbol(symbol):  # class/interface/enum
                    cls = _symbol_to_java_class(file_path, symbol)
```

---

## 八、扩展 LSP 支持

### 8.1 添加新语言步骤

1. 在 `config.py` 中添加 `Language` 枚举值
2. 在 `get_language_config()` 中添加配置
3. 在 `client.py` 中扩展 `_parse_*` 方法（如需要）

### 8.2 多语言架构注意事项

当前架构是单语言的（`LspClient` 持有单一 `Language`）。

未来多语言支持需要考虑：

```
方案 A: 每个语言一个 LspClient 实例
方案 B: LspClient 支持动态 Language
方案 C: 抽象 LanguageServer 接口，每个语言实现
```

当前代码中，`LspClient` 的 `language` 参数主要用于：
- 选择正确的初始化选项
- 设置 `textDocument/didOpen` 的 `languageId`
- 确定 JDT LS workspace（仅 Java）

扩展到多语言时，这些逻辑需要相应调整。

---

## 九、错误处理

### 9.1 LSP 不可用

```python
# errors.py
class LspUnavailableError(EngineError):
    """Raised when LSP server is not available for required operations."""
```

当 `apply rename_field` 时如果 jdtls 不在 PATH 上，会抛出此错误。

### 9.2 LSP 通信错误

```python
# client.py 第436-437行
if "error" in message:
    future.set_exception(RuntimeError(f"LSP error: {message['error']}"))
```

### 9.3 rename 失败

```python
# execution_engine.py
rename_range = await client.prepare_rename(source_path, position)
if rename_range is None:
    raise EngineError(ErrorType.VALIDATION_FAILED,
                       "LSP rejected this location for rename")
```

---

## 十、推荐阅读顺序

```
1. src/core/lsp/config.py          → 了解如何配置语言服务器
2. src/core/lsp/client.py           → 理解 LSP 通信机制（重点读 lifecycle 和请求模式）
3. src/core/engine/execution_engine.py → 看 rename 如何调用 LSP（_request_lsp_rename 和 _build_rename_patches）
4. src/core/parser/java_parser.py   → 看 LSP 如何用于代码解析（_analyze_with_lsp）
5. src/cli/commands/plan.py        → 看 CLI 如何触发 LSP 操作
```

---

## 十一、关键文件索引

| 文件 | 行数 | 核心职责 |
|------|------|----------|
| `src/core/lsp/client.py` | 544 | JSON-RPC 客户端，LSP 请求/响应 |
| `src/core/lsp/config.py` | 155 | 语言服务器配置 |
| `src/core/engine/execution_engine.py` | 350 | rename 操作执行管线 |
| `src/core/parser/java_parser.py` | 530 | LSP + 静态解析的 Java 分析器 |
| `src/core/engine/errors.py` | - | LSP 相关错误定义 |

---

## 十二、未来扩展方向

1. **多语言支持**: 添加 Python (pyright)、TypeScript (tsserver) 等
2. **VS Code 插件**: Python 后端 + TypeScript thin client（见下方架构图）
3. **增量解析**: 替代当前的全量重建策略
4. **LSP 缓存**: 避免每次都重新启动 jdtls

### VS Code 插件推荐架构

```
┌─────────────────────────────────────────────────────────┐
│  VS Code Extension (TypeScript thin shell)              │
│  ├── src/extension.ts     → spawn Python 进程           │
│  └── src/bridge.ts       → JSON-RPC over stdio 转发     │
└─────────────────────────┬───────────────────────────────┘
                          │ stdio
┌─────────────────────────▼───────────────────────────────┐
│  Voyager Python Backend (现有代码)                       │
│  ├── LspClient         → 已实现                          │
│  ├── ExecutionEngine   → 已实现                          │
│  └── 新增: src/server/main.py → stdio server            │
└─────────────────────────────────────────────────────────┘
```
