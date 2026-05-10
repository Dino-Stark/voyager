# Voyager V1 Server Mode

## 背景

V1 最初的 CLI 模型是一次命令完成一次工作：`scan`、`plan`、`apply` 各自在自己的进程里初始化所需状态。这个模型对静态 parser 影响不大，但对 JDT LS 不合适。

JDT LS 是一个需要启动、初始化、索引、维护 workspace 的长期服务。把 Voyager 做成一次性命令会导致几个问题：

- 每次命令都可能重新启动和关闭 JDT LS，启动成本高。
- JDT LS 在 Windows 上 shutdown 时容易输出噪声日志，甚至触发编码/日志异常。
- `scan -> plan -> apply` 本质上是同一个项目会话，拆成多个短进程会丢失已经 warm up 的 LSP 状态。
- 后续接 IDE、Agent、TUI 时，CLI 不应该是唯一执行入口。

因此这次改造把 Voyager 从“命令直接执行核心逻辑”调整为“Server 执行，CLI 只是 Client”。

---

## 目标形态

```text
Voyager Clients
  - CLI
  - IDE plugin
  - Agent
  - future UI/TUI

        |
        | local JSON request
        v

Voyager Server
  - owns ProjectSession
  - owns JDT LS lifecycle
  - owns ExecutionEngine
  - serializes semantic operations
  - reads/writes .voyager derived state

        |
        | LSP JSON-RPC over stdio
        v

JDT LS
```

Server 是项目级别的：一个 Java project root 对应一个 Voyager Server。CLI 命令可以短生命周期退出，但 Server 和它持有的 JDT LS 可以持续运行。

---

## 用户命令

显式后台启动：

```bash
voyager start [project_path]
```

显式前台运行：

```bash
voyager serve [project_path]
```

普通本地使用时，`scan/plan/apply` 会自动启动 Server：

```bash
voyager start .
voyager scan .
voyager plan rename UserDTO.userName customerName
voyager apply -y
voyager stop
```

状态查看：

```bash
voyager status
```

`status` 会显示当前项目的 graph 信息和 Server 是否运行。

---

## 核心代码结构

```text
src/core/server/
├── protocol.py     # ServerInfo、协议常量、operation 反序列化
├── server.py       # VoyagerServer，持有 ProjectSession
└── client.py       # VoyagerServerClient，供 CLI/未来 IDE/Agent 调用

src/core/session/
├── project_session.py  # 长生命周期项目会话，持有 LspClient + ExecutionEngine
└── daemon.py           # 旧 daemon 名称兼容层

src/voyager_cmd/
├── main.py        # CLI: start/serve/scan/plan/apply/status/stop
├── server.py      # python -m voyager_cmd.server 入口
└── daemon.py      # 旧 daemon 入口兼容层
```

主路径是 `core.server`。`core.session.daemon` 和 `voyager_cmd.daemon` 只保留兼容，不再代表新的架构概念。

---

## 生命周期

### 1. 第一次 CLI 请求

```text
voyager start .
  -> VoyagerServerClient(project_path).start()
  -> read .voyager/cache/server.json
  -> no running server
  -> start background process:
     python -m voyager_cmd.server <project_path>
```

### 2. Server 启动

```text
voyager_cmd.server
  -> run_server(project_path)
  -> VoyagerServer.run()
  -> ProjectSession.start()
  -> LspClient(Language.JAVA).start()
  -> start local TCP server
  -> write .voyager/cache/server.json
```

`scan/plan/apply` 仍然会在没有 Server 时自动启动项目级 Server；`start` 只是把这个生命周期动作显式化，不会构建 semantic graph。

`ProjectSession` 是真正的长期状态容器：

- `LspClient`：JDT LS 进程和 LSP 通信状态。
- `ExecutionEngine`：plan/apply 管线。
- `StorageManager`：graph、pending plan、operation log、server state。

### 3. 后续 CLI 请求

```text
voyager plan rename UserDTO.userName customerName
  -> read server.json
  -> ping server
  -> reuse existing Server and JDT LS
  -> operation/plan
```

`apply` 同理复用已启动的 JDT LS，不再为每个命令重启语言服务器。

### 4. 停止

```text
voyager stop
  -> server/shutdown
  -> ProjectSession.close()
  -> LspClient.shutdown()
  -> clear .voyager/cache/server.json
```

---

## 本地协议

当前协议是 newline-delimited JSON over localhost TCP。它不是最终必须形态，但足够支撑本地 CLI、Agent、IDE 插件的第一阶段集成。

请求格式：

```json
{
  "id": 123,
  "method": "operation/plan",
  "params": {
    "operation": {
      "op": "rename_field",
      "target": "UserDTO.userName",
      "to": "customerName"
    }
  },
  "token": "..."
}
```

响应格式：

```json
{
  "id": 123,
  "result": {
    "is_valid": true,
    "affected_files": []
  }
}
```

错误格式：

```json
{
  "id": 123,
  "error": {
    "type": "ValueError",
    "message": "Unknown Voyager server method: ..."
  }
}
```

当前方法：

| Method | 说明 |
| --- | --- |
| `server/ping` | 健康检查，不需要占用 ProjectSession 锁 |
| `server/status` | 返回 Server 是否运行、pid、project path |
| `project/scan` | 解析项目并重建 semantic graph |
| `operation/plan` | 对 operation 做前置校验和影响范围计算 |
| `operation/apply` | 执行 operation，写盘前后做校验 |
| `server/shutdown` | 停止 Server 和它持有的 JDT LS |

---

## 状态文件

Server 发现信息写在项目内：

```text
.voyager/cache/server.json
```

示例：

```json
{
  "pid": 19400,
  "host": "127.0.0.1",
  "port": 7003,
  "token": "...",
  "project_path": "D:\\Project\\examples\\shop-dto",
  "protocol": "voyager-jsonrpc-v1"
}
```

`server.json` 只用于发现本地 Server，不用于请求/响应。真正通信走 localhost TCP。

日志写到：

```text
.voyager/cache/server.log
```

旧的 `.voyager/cache/session.json` 是 daemon 时代的状态文件。当前 client 会兼容读取并尝试关闭旧 daemon，然后启动新的 Server。

---

## 并发模型

Server 可以接受多个 client 连接，但 semantic operation 必须串行执行。

原因：

- `ProjectSession` 持有同一个 `ExecutionEngine`。
- `LspClient` 对应同一个 JDT LS 进程。
- `scan/apply` 会更新 `.voyager/graph.json` 和内存 graph。

因此 `VoyagerServer` 内部用一个 request lock 串行化这些方法：

- `project/scan`
- `operation/plan`
- `operation/apply`
- `server/shutdown`

`server/ping` 和 `server/status` 不占用这个锁，避免长时间 `scan/apply` 时健康检查被阻塞，导致 client 误判 Server 已死并启动第二个 Server。

---

## scan / plan / apply 的新调用链

### scan

```text
CLI scan
  -> VoyagerServerClient.scan()
  -> project/scan
  -> ProjectSession.scan()
  -> parse_java_project_async(lsp_client=reused_client)
  -> GraphBuilder.build()
  -> StorageManager.save_graph()
```

### plan

```text
CLI plan
  -> build Operation model
  -> VoyagerServerClient.plan(operation)
  -> operation/plan
  -> ProjectSession.plan()
  -> ExecutionEngine.plan_async()
  -> validate_pre
  -> compute affected files
  -> CLI saves pending_plan.json
```

### apply

```text
CLI apply
  -> read pending_plan.json
  -> VoyagerServerClient.apply(operation)
  -> operation/apply
  -> ProjectSession.apply()
  -> ExecutionEngine.apply_async()
  -> validate_pre
  -> LSP prepareRename + rename
  -> apply edits in memory
  -> rebuild graph
  -> validate_post
  -> commit files
  -> save graph + operation log
```

---

## JDT LS 生命周期变化

改造前：

```text
voyager scan
  -> start JDT LS
  -> scan
  -> shutdown JDT LS

voyager apply
  -> start JDT LS again
  -> rename
  -> shutdown JDT LS again
```

改造后：

```text
voyager start
  -> start Voyager Server
  -> start JDT LS once

voyager scan
  -> scan

voyager plan
  -> reuse Server

voyager apply
  -> reuse Server and JDT LS

voyager stop
  -> shutdown JDT LS once
  -> stop Server
```

这个模型更接近 IDE 和 Agent 的运行方式：项目上下文持续存在，命令只是对上下文发请求。

---

## affected files 逻辑修正

Server 化验证时暴露了一个旧问题：`plan` 对 `rename_field` 的影响文件估计偏小。

示例：

```java
String name = user.getUserName();
```

JDT LS 在 rename field `userName -> customerName` 时会同步改 JavaBean getter/setter：

```java
String name = user.getCustomerName();
```

旧的 semantic graph 只记录 field access 和类型引用，因此 `plan` 可能只显示 `UserDTO.java`，但 `apply` 实际会修改 `OrderService.java`、`UserDTO.java`、`UserService.java`。

这次补了两层：

- `GraphBuilder` 增加保守的 typed method call reference。
- `SemanticGraph.get_affected_files_for_field()` 会把 JavaBean accessor 方法及其调用文件纳入 affected files。

这样 `plan` 的输出更接近 JDT LS rename 的真实修改范围。

---

## 兼容策略

为了避免一次重构破坏旧入口，保留兼容层：

- `src/core/session/daemon.py`
  - `VoyagerDaemonClient = VoyagerServerClient`
  - `VoyagerDaemonServer = VoyagerServer`
  - `run_daemon()` 转到 `run_server()`

- `src/voyager_cmd/daemon.py`
  - 旧 `python -m voyager_cmd.daemon <project_path>` 仍然能启动新 Server。

未来如果确认没有旧引用，可以删除兼容层。

---

## 验证方式

单元测试：

```bash
python -m compileall -q src tests
python -m pytest -q
```

手工流程：

```bash
python examples/reset.py shop-dto
cd examples/shop-dto
voyager -v start .
voyager -v scan .
voyager plan rename UserDTO.userName customerName
voyager apply -y
voyager stop
```

期望：

- `start` 显式启动项目级 Server，后续 `scan/plan/apply` 复用同一个 Server。
- 如果没有提前 `start`，`scan/plan/apply` 仍会自动启动当前项目的 Server。
- `plan` 报 3 个 affected files：
  - `src/main/java/com/shop/OrderService.java`
  - `src/main/java/com/shop/UserDTO.java`
  - `src/main/java/com/shop/UserService.java`
- `apply` 修改同样 3 个文件。
- `stop` 后 `.voyager/cache/server.json` 被清理。

---

## 后续方向

当前协议是本地最小实现。下一步可以演进，但不急着引入复杂基础设施：

- 增加 progress notification，用于长时间 scan/index。
- 增加 cancel request，用于取消长任务。
- 增加轻量级 registry/broker，便于 IDE/Agent 发现多个项目级 Server，但保持一个 project root 对应一个 Server 的隔离边界。
- 为 IDE/Agent 暴露更稳定的 JSON-RPC schema。
- 把 Server 集成测试扩展到真实 CLI 自动启动路径。

核心边界不变：Server 是执行者，CLI/IDE/Agent 都是 Client。
