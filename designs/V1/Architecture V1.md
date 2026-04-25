# Voyager V1 架构设计（POC）

## 🎯 目标（V1 Scope）

实现一个最小可用系统，支持：

> **安全地对 DTO 字段进行重命名，并自动更新所有引用（跨文件一致性）**

---

## 🚫 V1 不包含

* 多语言支持
* 多 Agent 协作
* 自动架构设计
* 完整 call graph
* VS Code 插件
* 自我进化（skill evolution）

---

## 🧱 整体架构

```
voyager/
├── cmd/                     # CLI 入口
├── core/                    # 核心逻辑
│   ├── parser/              # AST 解析（JavaParser）
│   ├── graph/               # 语义图构建
│   ├── operation/           # 操作定义（rename 等）
│   ├── engine/              # 执行引擎（核心）
│   ├── diff/                # diff 生成
│   └── rules/               # 规则系统
├── storage/
│   └── .voyager/              # 本地状态存储
├── cli/
│   └── commands/            # CLI 命令实现
└── utils/
```

---

## 📦 .voyager 目录结构

```
.voyager/
├── graph.json          # 语义图（核心）
├── index.json          # symbol索引（可选）
├── operations.log      # 操作历史（可选）
├── rules.yaml          # 项目规则
└── cache/              # AST缓存（可选）
```

---

## 🧠 核心模块说明

### 1. Parser（AST解析）

基于 JavaParser：

职责：

* 解析 Java 文件
* 提取 class / field / method
* 提供基础 AST 访问能力

---

### 2. Semantic Graph（语义图）

最小结构：

```json
{
  "symbols": [
    {
      "id": "OrderDTO",
      "type": "class",
      "fields": ["id", "amount"],
      "file": "order/OrderDTO.java"
    }
  ],
  "references": [
    {
      "from": "OrderService.create",
      "to": "OrderDTO"
    }
  ]
}
```

---

### 3. Operation Spec（操作协议）

V1支持：

```json
[
  "add_field",
  "remove_field",
  "rename_field",
  "update_api",
  "add_function",
  "update_function_signature"
]
```

重点实现：

```json
{
  "op": "rename_field",
  "target": "OrderDTO.userId",
  "to": "customerId"
}
```

---

### 4. Operation Engine（执行引擎）

执行流程：

```
Plan
 → Validate（规则）
 → Apply（内存修改）
 → Re-parse（重新生成 graph）
 → Validate again
 → Commit（写文件）
```

---

### 5. Diff Engine

职责：

* 比较修改前后代码
* 生成结构化 diff

输出示例：

```json
{
  "file": "OrderDTO.java",
  "diff": "...",
  "status": "pending"
}
```

---

### 6. Rules Engine（规则系统）

职责：

> 检测错误 + 阻断执行

---

#### 示例规则：DTO 重复定义

```yaml
rules:
  - id: dto_duplicate
    type: symbol_uniqueness
    target: DTO
    action: error
```

---

#### 检测逻辑

* same_name + different_structure → ❌ error
* different_name + same_structure → ⚠️ warn

---

### 7. Reference System（引用关系）

定义：

> 能被类型系统解析的引用

包括：

* 类型引用
* 方法参数
* 返回值

不包括：

* 字符串
* 注释

---

## ⚙️ CLI 设计（V1）

示例：

```bash
voyager plan rename OrderDTO.userId customerId
```

输出：

```json
{
  "operations": [...],
  "affected_files": [...],
  "violations": []
}
```

---

执行：

```bash
voyager apply
```

---

## ❗一致性策略（必须）

采用：

> **强一致（All-or-nothing）**

规则：

* 任意失败 → 回滚
* 不允许部分成功

---

## ❗失败处理

返回结构化错误：

```json
{
  "error": {
    "type": "symbol_not_found",
    "target": "OrderDTO.userId",
    "file": "OrderService.java"
  }
}
```

---

## ⚠️ V1 限制（非常重要）

仅支持：

* 普通 POJO DTO
* 明确类型引用
* 无反射 / 动态代理

不支持：

* Spring 自动注入分析
* Lombok（建议禁用）
* 深层泛型解析

---

## 🔁 Graph 更新策略

V1：

> 全量重建（保证正确）

后续优化：

> 增量更新

---

## 🧪 V1 验收标准

系统必须做到：

* rename_field 100%成功（跨文件）
* 无漏改
* 无误改
* 修改后代码可编译

---

## 🚀 开发顺序（强烈建议）

1. JavaParser 解析 DTO
2. 构建 symbol graph
3. 实现 reference 查找
4. 实现 rename_field（核心）
5. 生成 diff
6. 应用修改 + 编译验证
7. 加入规则系统
8. CLI 封装

---

## 🧩 后续扩展方向（非V1）

* call graph
* 微服务架构图
* 多语言支持
* Agent Planner
* VS Code 插件

---

## 🧠 核心原则（必须遵守）

1. 操作基于“语义”，不是文本
2. 所有修改必须可回滚
3. 不追求智能，优先保证正确
4. 不确定的情况一律拒绝执行

---
