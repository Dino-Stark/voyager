# Voyager V1 设计决策与约束（Design Decisions & Constraints）

## 🎯 本文目的

记录 Voyager V1 阶段已经确定的核心设计决策，避免：

* 反复推翻基础抽象
* 过度设计
* 复杂度失控

---

# 一、核心问题定义

Voyager 要解决的不是：

> “如何生成更多代码”

而是：

> **“如何保证代码修改的全局一致性”**

---

## ❗关键问题抽象

当前 coding agent 的核心问题：

* 修改是“局部的”
* 缺乏全局结构感知
* 无法保证跨文件一致性

---

## ✅ Voyager 的核心目标

> **将代码修改从“文本操作”升级为“语义操作”**

---

# 二、核心设计原则（必须遵守）

## 1️⃣ 语义优先（Semantic-first）

* 所有操作必须基于语义（symbol / AST）
* 禁止纯字符串替换

---

## 2️⃣ 正确性优先于智能（Correctness > Intelligence）

* 系统可以“保守”，但不能“错误”
* 不确定 → 拒绝执行

---

## 3️⃣ 强一致性（Strong Consistency）

* 所有修改必须：

  * 要么全部成功
  * 要么全部回滚

---

## 4️⃣ 可回滚（Reversible）

* 每次操作必须可撤销
* 必须能恢复到执行前状态

---

## 5️⃣ 可验证（Verifiable）

* 每次修改后：

  * 必须重新解析
  * 必须通过规则检查

---

# 三、系统边界（V1 Scope Control）

## ✅ V1只解决：

> **结构化代码修改的一致性问题**

---

## ❌ V1明确不做：

* 自动架构设计
* 多 Agent 系统
* 自我进化（self-evolving skill）
* 完整调用链分析
* 微服务级建模
* IDE级复杂交互

---

👉 原则：

> **减少变量，保证闭环**

---

# 四、语义图（Semantic Graph）决策

## ✅ 数据来源

* AST（JavaParser）
* 静态代码分析

---

## ❗重要限制

* 不依赖运行时信息
* 不依赖框架（如 Spring）

---

## ❗设计原则

> **只保留“修改必须依赖”的信息**

---

## V1包含：

* symbols（class / DTO）
* fields
* references（类型级）

---

## V1不包含：

* 完整 call graph
* runtime dependency
* 动态行为

---

---

# 五、Reference 定义

## ✅ 定义

> **Reference = 可通过类型系统解析的引用**

---

## 包含：

* 类型引用
* 参数引用
* 返回值引用

---

## 不包含：

* 字符串
* 注释
* 动态反射

---

---

# 六、Operation 模型决策

## ✅ 操作本质

> **操作的是“语义结构”，不是文件文本**

---

## V1操作粒度

* 字段级（field-level）
* 方法签名级（signature-level）

---

## 示例

```json
{
  "op": "rename_field",
  "target": "OrderDTO.userId",
  "to": "customerId"
}
```

---

## ❗关键原则

* operation 必须：

  * 可验证
  * 可回滚
  * 可组合

---

---

# 七、执行模型（Execution Model）

## 固定流程（不可改变）

```text
Plan
 → Validate（规则）
 → Apply（内存修改）
 → Re-parse（生成新 graph）
 → Validate again
 → Commit（写文件）
```

---

## ❗禁止行为

* 边修改边写文件
* 局部失败继续执行

---

---

# 八、规则系统（Rules Engine）

## ✅ 职责

> **检测错误 + 阻断执行**

---

## ❗不负责：

* 自动修复
* 智能推理
* 业务逻辑判断

---

## 示例规则

* DTO 重复定义
* 非法依赖（未来）
* API未同步（未来）

---

## DTO重复判断（V1）

规则：

* same_name + different_structure → error
* different_name + same_structure → warn

---

---

# 九、失败处理策略

## ✅ 原则

* 失败立即停止
* 不允许部分成功

---

## 返回结构

```json
{
  "error": {
    "type": "xxx",
    "message": "xxx",
    "location": "xxx"
  }
}
```

---

## ❗限制

* 最多允许一次自动重试
* 禁止无限重试

---

---

# 十、存储设计（.voyager 目录）

## 角色

> **派生状态（Derived State），不是源数据**

---

## 数据来源

* 从代码解析生成
* 不允许手动维护

---

## 原则

* 可重建
* 与 git 状态一致

---

---

# 十一、复杂度控制策略（关键）

## ❗最大风险

> 系统复杂度失控

---

## 控制方法

### 1. 延迟设计

* 不为未来功能设计结构
* 只实现当前需求

---

### 2. 单一能力优先

V1只聚焦：

> rename_field 跨文件一致性

---

### 3. 拒绝“聪明但不稳定”的能力

* 自动修复 ❌
* 模糊匹配 ❌
* 猜测逻辑 ❌

---

---

# 十二、LLM 的角色定位

## ❗关键结论

> **LLM 不负责保证正确性**

---

## LLM负责：

* 生成操作建议（未来）
* 辅助分析

---

## 系统负责：

* 校验
* 执行
* 一致性保证

---

---

# 十三、未来扩展（但不提前实现）

以下能力已确认，但不在V1实现：

---

## 架构层

* service graph
* API dependency

---

## 分析能力

* call graph（增强版）
* 跨服务调用

---

## Agent能力

* Planner Agent
* Architect Agent

---

## UI

* VS Code 插件
* 可视化架构图

---

---

# 十四、一句话总结

Voyager 的本质是：

> **构建一个“可控的代码修改系统”，而不是一个“更聪明的代码生成系统”**

---
