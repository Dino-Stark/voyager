# Voyager

> 将代码修改从"文本操作"升级为"语义操作"

Voyager 是一个语义级的代码修改系统，保证代码修改的全局一致性。

## V1 目标

安全地对 DTO 字段进行重命名，并自动更新所有引用（跨文件一致性）。

## 依赖要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | >= 3.10 | Voyager 运行基础 |
| JDK | 17+ | JDTLS 需要独立的 JDK 运行环境 |
| 网络连接 | - | 首次安装时下载 JDT Language Server |

## 快速开始

### 1. 安装

```bash
# 安装 Python 包
pip install -e .

# 下载并安装 JDT Language Server (Java 语义分析引擎)
python -m scripts.setup_jdtls
```

### 2. 验证安装

```bash
# 检查 JDTLS 安装状态
python -m scripts.setup_jdtls --check
```

### 3. 分析项目

```bash
voyager scan /path/to/java/project
```

### 4. 规划重命名

```bash
voyager plan rename OrderDTO.userId customerId
```

### 5. 执行修改

```bash
voyager apply
```

## JDTLS 安装说明

Voyager 使用 Eclipse JDT Language Server 进行 Java 语义分析。`setup_jdtls.py` 脚本会自动检测操作系统和架构，下载对应版本的 JDTLS (~150MB)。

| 参数 | 说明 |
|------|------|
| `--check` | 检查安装状态 |
| `--os` | 指定操作系统 (windows/linux/darwin) |
| `--arch` | 指定架构 (x64/arm64) |
| `--force` | 强制重新安装 |

```bash
# 示例：指定平台安装
python -m scripts.setup_jdtls --os windows --arch x64

# 强制重新安装
python -m scripts.setup_jdtls --force
```

## 核心原则

1. **语义优先** - 所有操作基于 symbol/AST，禁止纯字符串替换
2. **正确性 > 智能** - 不确定则拒绝执行
3. **强一致性** - All-or-nothing，任意失败则回滚
4. **可回滚** - 每次操作可撤销
5. **可验证** - 修改后重新解析并通过规则检查

## V1 限制

- 仅支持普通 POJO DTO
- 仅支持明确类型引用
- 不支持反射/动态代理
- 不支持 Lombok / Spring 自动注入分析

## 参考文档

- [LSP 3.18 Specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.18/specification/) — Language Server Protocol 官方协议定义
- [LSP Specification (main)](https://microsoft.github.io/language-server-protocol/) — 稳定版规范入口
- [Eclipse JDT Language Server](https://github.com/eclipse-jdtls/eclipse.jdt.ls) — Voyager V1 使用的 Java 语言服务器
- [LSP documentSymbol](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.18/specification/#textDocument_documentSymbol) — 项目解析使用的 LSP 方法
- [LSP textDocument/rename](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.18/specification/#textDocument_rename) — 字段重命名使用的 LSP 方法
- `designs/V1/LSP Architecture.md` — 项目内 LSP 架构详解文档
