# Voyager V1 目录结构

## 顶层结构

```
voyager/
├── pyproject.toml          # 项目配置（依赖/构建/CLI入口）
├── README.md               # 项目说明文档
├── designs/                # 设计文档
│   └── V1/
├── src/                    # 项目源代码
│   ├── voyager_cmd/        # CLI 入口
│   ├── core/               # 核心逻辑
│   ├── cli/                # CLI 命令实现
│   ├── storage/            # 存储管理
│   └── utils/              # 工具函数
├── examples/               # 示例代码
└── tests/                  # 单元测试
```

---

## src/ 源代码详解

### voyager_cmd/ — CLI 入口

```
src/voyager_cmd/
├── __init__.py
└── main.py                 # Click CLI 定义，注册 scan/plan/apply/status 命令
```

基于 [Click](https://click.palletsprojects.com/) 框架，提供 `voyager` 命令行入口。

---

### core/ — 核心逻辑

```
src/core/
├── __init__.py
├── lsp/                    # ★ LSP 客户端驱动器（通用架构）
│   ├── __init__.py
│   ├── client.py           # 通用 LSP 客户端（JSON-RPC over stdio）
│   │                        #   - LspClient: 驱动任意语言服务器
│   │                        #   - get_symbols()        提取文件符号
│   │                        #   - get_references()     查找引用（影响分析）
│   │                        #   - rename_symbol()      语义级重命名
│   │                        #   - find_definitions()   跳转到定义
│   │                        #   - find_implementations() 查找实现
│   │                        #   - get_diagnostics()    代码校验
│   └── config.py           # 语言服务器配置
│                            #   - LanguageConfig: 各语言 server 启动命令
│                            #   - Java: jdtls (Eclipse JDT Language Server)
│                            #   - TODO: Python (pyright), TS, Go, C++, C#
├── parser/
│   ├── __init__.py
│   └── java_parser.py      # Java 代码分析（基于 LSP jdt.ls）
│                            #   - JavaClass / JavaField / JavaMethod 数据模型
│                            #   - parse_java_project()  通过 LSP 解析整个项目
│                            #   - DTO 启发式识别
├── graph/
│   ├── __init__.py
│   ├── semantic_graph.py   # 语义图数据模型（Pydantic）
│                            #   - Symbol: class / field / method
│                            #   - Reference: 类型引用 / 参数引用 / 返回值引用
│                            #   - SemanticGraph: 符号索引 + 引用查询
│   └── builder.py          # 语义图构建器
│                            #   - 从 JavaClass 列表构建完整语义图
│                            #   - FQCN 解析 + 类型引用提取
├── operation/
│   ├── __init__.py
│   └── models.py           # 操作模型（Pydantic）
│                            #   - RenameFieldOp  字段重命名
│                            #   - AddFieldOp     添加字段
│                            #   - RemoveFieldOp  删除字段
│                            #   - PlanResult / ApplyResult  结果模型
├── engine/
│   ├── __init__.py
│   ├── execution_engine.py # 执行引擎（核心）
│                            #   - 严格管线: Plan → Validate → Apply → Re-parse → Validate → Commit
│                            #   - rename 操作委托 LSP textDocument/rename
│                            #   - 强一致性: All-or-nothing，失败即回滚
│                            #   - V1 策略: 全量重建 graph
│   └── errors.py           # 结构化错误定义
│                            #   - EngineError 基类
│                            #   - SymbolNotFoundError / RuleViolationError / ValidationError 等
├── diff/
│   ├── __init__.py
│   └── diff_engine.py      # Diff 生成
│                            #   - 基于 difflib 生成 unified diff
│                            #   - FileDiff 数据模型 + 摘要格式化
└── rules/
    ├── __init__.py
    └── validator.py        # 规则校验
                             #   - 预执行校验 (validate_pre)
                             #   - 后执行校验 (validate_post)
                             #   - DTO 重复定义检测 (same_name + diff_structure → error)
                             #   - 自定义规则加载 (rules.yaml)
```

---

### cli/ — CLI 命令实现

```
src/cli/
├── __init__.py
└── commands/
    ├── __init__.py
    ├── scan.py             # voyager scan <project_path>
    │                        #   通过 LSP 解析 Java 项目 → 构建语义图 → 持久化到 .voyager/
    ├── plan.py             # voyager plan rename|add_field|remove_field <target> [value]
    │                        #   校验操作 → 计算影响范围 → 保存 pending_plan.json
    └── apply.py            # voyager apply [--yes]
                             #   加载 pending_plan → 通过 LSP 执行 → 写文件 → 清理 plan
```

---

### storage/ — 存储管理

```
src/storage/
├── __init__.py
└── manager.py              # .voyager/ 目录管理
                             #   - graph.json        语义图持久化
                             #   - operations.log    操作历史记录
                             #   - rules.yaml        项目规则配置
                             #   - cache/            LSP workspace 缓存
                             #   - pending_plan.json 待执行计划
```

---

### utils/ — 工具函数

```
src/utils/
└── __init__.py             # 预留工具模块
```

---

## .voyager/ 运行时目录（派生状态）

由 `storage/manager.py` 自动管理，**不允许手动维护**：

```
.voyager/
├── graph.json              # 语义图（核心数据）
├── pending_plan.json       # 待执行的操作计划
├── operations.log          # 操作历史日志
├── rules.yaml              # 项目规则配置（可选）
└── cache/                  # LSP workspace 缓存
    └── jdtls-workspace/    # jdt.ls 工作空间
```

---

## 依赖说明

| 包 | 用途 |
|---|---|
| `click>=8.1` | CLI 框架 |
| `pydantic>=2.0` | 数据模型定义与校验 |
| `pyyaml>=6.0` | 规则文件解析 |
| `rich>=13.0` | 终端美化输出 |

LSP 客户端使用 Python 标准库（`asyncio`, `subprocess`, `json`），无额外依赖。

语言服务器需**单独安装**：

| 语言 | 服务器 | 安装方式 |
|---|---|---|
| **Java** | jdt.ls | 系统 package manager 或 [eclipse.jdt.ls](https://github.com/eclipse-jdtls/eclipse.jdt.ls) |
| Python | pyright | TODO: `npm install -g pyright` |
| TypeScript | typescript-language-server | TODO: `npm install -g typescript-language-server typescript` |
| Go | gopls | TODO: `go install golang.org/x/tools/gopls@latest` |
| C/C++ | clangd | TODO: 系统 package manager |
| C# | OmniSharp | TODO: 下载 [OmniSharp](https://github.com/OmniSharp/omnisharp-roslyn) |

开发依赖：`pytest` / `pytest-cov` / `ruff`

---

## LSP 架构说明

Voyager 采用 **"一个通用 LSP 客户端 + 动态切换后端服务器"** 的架构：

```
┌──────────────────────────────────────┐
│           Voyager Agent              │
│  ┌────────────────────────────────┐  │
│  │    LspClient (通用客户端)       │  │
│  │  - JSON-RPC over stdio         │  │
│  │  - 统一的 LSP 协议接口          │  │
│  └──────────┬─────────────────────┘  │
│             │                        │
│    ┌────────┴────────────────┐       │
│    │     LanguageConfig      │       │
│    │  ┌──────┬──────┬──────┐ │       │
│    │  │ jdtls│pyright│clangd│...│   │
│    │  └──────┴──────┴──────┘ │       │
│    └─────────────────────────┘       │
└──────────────────────────────────────┘
```

- **Client 端**：`LspClient` 不关心后台是哪种服务器，只发送标准的 LSP 请求
- **配置驱动**：`config.py` 中定义各语言的服务器启动命令和初始化参数
- **元指令封装**：高层面 API 如 `get_impact_analysis()`、`rename_symbol()` 等

---

## 备注

- `voyager_cmd/` 原设计为 `cmd/`，因 Windows 上与系统模块冲突而改名
- 所有源码包位于 `src/` 下，通过 `pyproject.toml` 的 `[tool.setuptools.packages.find].where = ["src"]` 配置发现
- LSP 解析替代了原先的 `javalang` 库，提供工业级的 Java 语义分析能力
