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
├── parser/
│   ├── __init__.py
│   └── java_parser.py      # Java AST 解析（基于 javalang）
│                            #   - JavaClass / JavaField / JavaMethod 数据模型
│                            #   - parse_java_file()  解析单个文件
│                            #   - parse_java_project()  解析整个项目
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
    │                        #   解析 Java 项目 → 构建语义图 → 持久化到 .voyager/
    ├── plan.py             # voyager plan rename|add_field|remove_field <target> [value]
    │                        #   校验操作 → 计算影响范围 → 保存 pending_plan.json
    └── apply.py            # voyager apply [--yes]
                             #   加载 pending_plan → 执行 → 写文件 → 清理 plan
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
                             #   - cache/            AST 缓存
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
└── cache/                  # AST 缓存（可选）
```

---

## 依赖说明

| 包 | 用途 |
|---|---|
| `click>=8.1` | CLI 框架 |
| `javalang>=0.13.0` | Java AST 解析 |
| `pydantic>=2.0` | 数据模型定义与校验 |
| `pyyaml>=6.0` | 规则文件解析 |
| `rich>=13.0` | 终端美化输出 |

开发依赖：`pytest` / `pytest-cov` / `ruff`

---

## 备注

- `voyager_cmd/` 原设计为 `cmd/`，因 Windows 上与系统模块冲突而改名
- 所有源码包位于 `src/` 下，通过 `pyproject.toml` 的 `[tool.setuptools.packages.find].where = ["src"]` 配置发现
