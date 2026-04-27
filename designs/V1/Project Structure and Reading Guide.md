# Voyager V1 项目结构与阅读顺序

这份文档按当前代码实现梳理项目结构。V1 的核心目标仍然很窄：围绕 Java POJO/DTO 的 `rename_field`，通过语义图、LSP rename、后验校验来保证跨文件一致性。

---

## 顶层结构

```text
voyager/
├── pyproject.toml          # 包配置、依赖、voyager CLI 入口
├── src/                    # 主代码
├── tests/                  # 当前 V1 的最小回归测试
├── designs/V1/             # V1 设计文档
└── examples/               # 示例占位
```

运行时状态不放在源码里，而是写到被扫描 Java 项目的 `.voyager/` 目录中：

```text
.voyager/
├── graph.json              # 派生出的语义图
├── pending_plan.json       # plan 后等待 apply 的操作
├── operations.log          # apply 成功后的操作日志
├── rules.yaml              # 可选规则配置
└── cache/                  # Voyager 本地缓存
```

JDT LS 的真实 workspace 不放在项目目录内，避免和 Java 项目发生 overlap；当前实现放到用户缓存目录：

```text
%LOCALAPPDATA%/Voyager/jdtls-workspaces/<project-hash>/
```

---

## src 结构

```text
src/
├── voyager_cmd/            # Click 命令入口
├── cli/commands/           # scan / plan / apply 命令实现
├── core/
│   ├── parser/             # Java 解析：LSP 优先，静态 parser 兜底
│   ├── graph/              # 语义图模型与构建
│   ├── operation/          # 操作协议模型
│   ├── engine/             # 执行管线与事务保证
│   ├── lsp/                # 通用 LSP 客户端与语言服务器配置
│   ├── rules/              # 前置/后置规则校验
│   └── diff/               # diff 工具
├── storage/                # .voyager 状态读写
└── utils/                  # 预留工具包
```

---

## 核心模块说明

### 1. CLI 入口

- `src/voyager_cmd/main.py`
- 注册 `voyager scan`、`voyager plan`、`voyager apply`、`voyager status`。
- 这里不做核心逻辑，只负责参数、输出、退出码。

### 2. CLI 命令实现

- `src/cli/commands/scan.py`
  - 调 `parse_java_project()` 解析 Java 项目。
  - 调 `GraphBuilder` 构建语义图。
  - 通过 `StorageManager` 保存 `.voyager/graph.json`。

- `src/cli/commands/plan.py`
  - 把 CLI 参数转成 `RenameFieldOp` 等 operation 模型。
  - 调 `ExecutionEngine.plan()` 做前置校验和影响文件计算。
  - 成功后保存 `.voyager/pending_plan.json`。

- `src/cli/commands/apply.py`
  - 读取 pending plan。
  - 调 `ExecutionEngine.apply()` 执行真实修改。
  - 成功后清理 pending plan。

### 3. Java 解析层

- `src/core/parser/java_parser.py`
- `parse_java_project()` 是入口。
- 解析策略：
  - 如果 `jdtls` 可用，先尝试 LSP `documentSymbol`。
  - 如果 LSP 返回不完整或失败，回退到内置静态 parser。
- 静态 parser 只面向 V1：普通 Java class、field、method、参数、返回值、简单 DTO 识别。

### 4. 语义图层

- `src/core/graph/semantic_graph.py`
  - 定义 `Symbol`、`Reference`、`SemanticGraph`。
  - symbol id 使用 FQN，例如 `com.acme.OrderDTO.userId`。
  - CLI 仍可用简单名，例如 `OrderDTO.userId`，前提是简单名不歧义。

- `src/core/graph/builder.py`
  - 从 `JavaClass` 列表构建图。
  - 记录 class / field / method。
  - 记录类型引用、参数引用、返回值引用、保守的 typed field access。

### 5. Operation 模型

- `src/core/operation/models.py`
- 当前核心是：

```json
{
  "op": "rename_field",
  "target": "OrderDTO.userId",
  "to": "customerId"
}
```

- `add_field`、`remove_field` 有模型，但 V1 执行层暂时不实现，避免扩大范围。

### 6. 执行引擎

- `src/core/engine/execution_engine.py`
- 这是 V1 最重要的文件。
- 固定管线：

```text
ensure graph
→ validate_pre
→ ask LSP for rename WorkspaceEdit
→ apply edits in memory
→ rebuild graph from in-memory files
→ validate_post
→ commit all files
```

- `rename_field` 必须走 JDT LS 的 `textDocument/rename`。
- 如果没有 `jdtls`，直接返回 `lsp_unavailable`，不会做字符串替换。
- 写盘前会做后验校验；如果发现旧字段访问残留，拒绝提交。

### 7. LSP 层

- `src/core/lsp/client.py`
  - JSON-RPC over stdio 的通用 LSP 客户端。
  - 支持 `documentSymbol`、`references`、`definition`、`implementation`、`prepareRename`、`rename`。
  - 解析 `WorkspaceEdit`，并把 LSP edits 转给执行引擎。

- `src/core/lsp/config.py`
  - 语言服务器配置。
  - 当前只启用 Java：`jdtls`。
  - Windows 下会优先解析 `jdtls.cmd/.bat/.exe`。

### 8. 规则层

- `src/core/rules/validator.py`
- `validate_pre()`：
  - 目标字段存在。
  - 新字段名不冲突。
  - 自定义规则检查。

- `validate_post()`：
  - rename 后新字段存在。
  - 旧字段定义不存在。
  - typed field access 中不能残留旧字段。
  - DTO 重复定义规则。

### 9. 存储层

- `src/storage/manager.py`
- 统一管理 `.voyager/`：
  - graph 保存/加载
  - pending plan 保存/加载/清理
  - operation log
  - rules path

### 10. 测试

- `tests/test_static_v1.py`
- 当前覆盖：
  - 静态 parser + graph 构建
  - 简单类名解析
  - LSP edits 应用顺序
  - 换行规范化
  - 无 `jdtls` 时安全拒绝 apply
  - rename 后旧字段访问残留检测

---

## 推荐阅读顺序

### 快速理解调用链

1. `src/voyager_cmd/main.py`
2. `src/cli/commands/scan.py`
3. `src/cli/commands/plan.py`
4. `src/cli/commands/apply.py`
5. `src/core/engine/execution_engine.py`

先按这个顺序读，可以直接看到用户命令如何走到核心执行管线。

### 理解数据模型

1. `src/core/operation/models.py`
2. `src/core/parser/java_parser.py`
3. `src/core/graph/semantic_graph.py`
4. `src/core/graph/builder.py`

这一组解释 Voyager 的“类 PSI 层”：不是直接把 LSP 暴露给 Agent，而是把 parser/LSP 的结果收敛成 operation、symbol、reference。

### 理解安全边界

1. `src/core/rules/validator.py`
2. `src/core/engine/errors.py`
3. `src/core/engine/execution_engine.py`

重点看失败时如何拒绝执行，以及为什么不做纯文本替换。

### 理解 LSP 接入

1. `src/core/lsp/config.py`
2. `src/core/lsp/client.py`
3. `src/core/engine/execution_engine.py` 中 `_request_lsp_rename()` 附近

这里能看到 JDT LS 如何启动、如何发送 rename 请求、如何把 WorkspaceEdit 转成内存 patch。

### 跑测试时读

1. `tests/test_static_v1.py`
2. `src/core/parser/java_parser.py`
3. `src/core/graph/builder.py`
4. `src/core/rules/validator.py`

测试用例是目前最短的行为说明。

---

## 当前 V1 的边界

- `scan/plan` 可以在没有 JDT LS 时用静态 parser 工作。
- `apply rename_field` 必须有 JDT LS。
- 不支持反射、动态代理、Lombok 生成代码、复杂 Spring 注入分析。
- public field 访问、getter/setter、方法调用等由 JDT LS rename 决定实际 edits；Voyager 会在提交前做后验校验，发现漏改就拒绝写盘。
- V1 不做完整 call graph，也不做多语言。

