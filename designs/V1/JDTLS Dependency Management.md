# JDTLS 依赖管理设计

> 本文档说明 Voyager 项目如何管理 JDT Language Server 依赖，实现跨平台、跨机器的可移植性。

---

## 1. 背景与问题

### 1.1 原始方案的问题

Voyager V1 依赖 Eclipse JDT Language Server 进行 Java 语义分析。最初方案将 JDTLS 安装在固定路径 `D:\Software\jdtls`，这导致：

| 问题 | 影响 |
|------|------|
| 硬编码绝对路径 | 换机器无法运行 |
| 人工手动安装 | 新开发者需要手动配置 |
| 路径不一致 | 团队成员路径可能不同 |
| Git 仓库污染 | 不应提交二进制文件到仓库 |

### 1.2 选择：下载脚本 vs 复制文件

| 方案 | 优点 | 缺点 |
|------|------|------|
| **下载脚本** | 仓库小、可自动更新版本、支持跨平台 | 首次需要网络 |
| 复制到 `scripts/` | 完全离线可用 | 仓库大 (~150MB)、平台固定 |

**最终选择：下载脚本方案**，理由：
1. Git 不适合管理大型二进制文件
2. JDTLS 版本更新时可通过修改脚本 URL 统一升级
3. 支持 Windows/macOS/Linux 自动检测

---

## 2. 架构设计

### 2.1 目录结构

```
voyager/
├── scripts/
│   ├── jdtls.cmd           # Windows launcher (git 管理)
│   ├── jdtls.sh            # Unix launcher (git 管理)
│   ├── setup_jdtls.py      # 安装脚本 (git 管理)
│   └── jdtls/              # 下载的 JDTLS (不提交到 git)
│       ├── bin/
│       │   └── jdtls       # JDTLS 可执行文件
│       └── config_linux/   # 配置目录
└── .gitignore              # 排除 scripts/jdtls/
```

### 2.2 组件职责

| 组件 | 职责 |
|------|------|
| `setup_jdtls.py` | 下载、解压、配置 JDTLS |
| `jdtls.cmd/.sh` | 平台特定的启动器，调用 `scripts/jdtls/bin/jdtls` |
| `.gitignore` | 排除 `scripts/jdtls/` 目录 |

### 2.3 启动流程

```
用户运行 jdtls.cmd
    ↓
jdtls.cmd 设置 JDTLS_BIN 路径
    ↓
调用 python "scripts/jdtls/bin/jdtls"
    ↓
JDTLS 启动并与 Voyager 通信
```

---

## 3. 实现细节

### 3.1 平台检测

```python
def get_system_info() -> tuple[str, str]:
    """自动检测操作系统和架构"""
    import platform

    os_map = {
        "windows": "windows",
        "darwin": "darwin",
        "linux": "linux",
    }
    arch_map = {
        "amd64": "x64",
        "x86_64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }

    os_key = os_map[platform.system().lower()]
    arch_key = arch_map[platform.machine().lower()]
    return os_key, arch_key
```

### 3.2 版本管理

JDTLS 版本号定义在脚本常量中：

```python
JDTLS_VERSION = "1.38.0"
DOWNLOAD_BASE = "https://download.eclipse.org/jdtls/milestones/1.38.0"
```

更新 JDTLS 版本只需修改这两个常量。

### 3.3 安装状态追踪

使用 `.voyager_installed` marker 文件记录安装信息：

```
version=1.38.0
platform=windows/x64
```

这允许：
- 快速检查是否已安装
- 验证版本是否需要更新
- 诊断安装问题

### 3.4 错误处理

| 场景 | 处理方式 |
|------|----------|
| 网络下载失败 | 显示错误信息，提示重试 |
| 磁盘空间不足 | 在下载前检查目标目录可用空间 |
| 提取失败 | 清理临时文件，提示错误 |
| 重复安装 | 询问是否强制覆盖 |

---

## 4. 使用方式

### 4.1 标准安装流程

```bash
# 1. 安装 Python 依赖
pip install -e .

# 2. 下载安装 JDTLS
python -m scripts.setup_jdtls

# 3. 验证安装
python -m scripts.setup_jdtls --check
```

### 4.2 命令行参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--check` | 检查安装状态 | `python -m scripts.setup_jdtls --check` |
| `--os` | 指定操作系统 | `--os windows` |
| `--arch` | 指定架构 | `--arch x64` |
| `--force` | 强制重新安装 | `python -m scripts.setup_jdtls --force` |

### 4.3 跨平台支持

| 平台 | 架构 | 下载 URL |
|------|------|----------|
| Windows | x64 | `...-win32.x64.tar.gz` |
| Linux | x64 | `...-linux.gtk.x86_64.tar.gz` |
| Linux | arm64 | `...-linux.gtk.aarch64.tar.gz` |
| macOS | x64 | `...-macos.x86_64.tar.gz` |
| macOS | arm64 | `...-macos.aarch64.tar.gz` |

---

## 5. 安全性考虑

### 5.1 下载验证 (TODO)

未来应添加 SHA256 校验：

```python
def verify_checksum(file_path: Path, expected_sha256: str) -> bool:
    """验证下载文件的完整性"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest() == expected_sha256
```

### 5.2 HTTPS

所有下载均使用 HTTPS，确保传输安全。

---

## 6. 未来扩展

### 6.1 自动更新检查

```python
def check_for_updates() -> bool:
    """检查是否有新版本可用"""
    response = urllib.request.get(API_URL)
    latest = response.json()["tag_name"]
    return latest != JDTLS_VERSION
```

### 6.2 离线模式支持

预下载 JDTLS 包到本地，通过 `--local` 参数指定本地文件路径：

```bash
python -m scripts.setup_jdtls --local ./jdtls-1.38.0.tar.gz
```

### 6.3 多语言支持

未来添加其他语言服务器时，可扩展为通用下载框架：

```
scripts/
├── setup_jdtls.py   # Java
├── setup_pyright.py # Python
├── setup_tsserver.py # TypeScript
└── servers/         # 统一的服务器目录
    ├── jdtls/
    ├── pyright/
    └── typescript/
```

---

## 7. 相关文件索引

| 文件 | 说明 |
|------|------|
| `scripts/setup_jdtls.py` | JDTLS 安装脚本实现 |
| `scripts/jdtls.cmd` | Windows 启动器 |
| `scripts/jdtls.sh` | Unix 启动器 |
| `.gitignore` | 排除下载的 JDTLS |
| `src/core/lsp/config.py` | Voyager 如何查找 jdtls 命令 |
