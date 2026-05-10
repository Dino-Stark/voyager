# Example Fixture Pattern — Source/Target 分离

## 问题

Example 项目中的 Java 代码在 Voyager 操作（如 rename_field）后会被修改，导致无法重复测试。每次运行前需要手动恢复原始文件。

## 方案

将 example 项目分为两层：

- **Source（源目录）**：`examples/_sources/<project>/` — 金本位（gold master），只读，永不修改
- **Target（运行目录）**：`examples/<project>/` — Voyager 的实际工作目录，可随意修改

每次测试前，通过 `reset.py` 脚本：删除 target 中所有文件 → 从 source 复制一份全新副本 → 开始测试。

## 目录结构

```text
examples/
  _sources/                    <- 源目录（只读）
    shop-dto/
      pom.xml
      src/main/java/com/shop/
        OrderDTO.java
        OrderService.java
        UserDTO.java
        UserService.java
    mini-customer/              <- 多项目隔离 smoke test fixture
    mini-order/                 <- 多项目隔离 smoke test fixture
  shop-dto/                    <- 运行目录（会被修改）
    pom.xml
    src/main/java/com/shop/
      ...
  mini-customer/               <- 运行目录（可重置）
  mini-order/                  <- 运行目录（可重置）
  reset.py                     <- 重置脚本
  README.md
```

## reset.py

```bash
# 重置单个项目
python examples/reset.py shop-dto

# 重置所有项目
python examples/reset.py
```

脚本行为：
1. 遍历 target 目录，删除其下所有文件和子目录（保留根目录本身，避免 Windows 文件锁问题）
2. 从 `_sources/<project>/` 复制全部内容到 `examples/<project>/`
3. 输出重置结果

## 核心原则

- **_sources/ 是只读的** — 绝不在测试流程中修改
- **运行目录是可抛弃的** — 任何状态（包括 `.voyager/`）都可被 reset 清除
- **每次测试前 reset** — 确保测试从一个干净的初始状态开始

## 添加新的 Example 项目

1. 在 `examples/_sources/` 下创建项目目录，放入原始代码
2. 运行 `python examples/reset.py <project_name>` 生成运行目录
3. 开始测试
