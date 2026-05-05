# 手动测试步骤 — shop-dto rename_field

## 测试项目：shop-dto

4 个 Java 类，包含跨文件引用关系：

| 类 | 字段 | 被引用于 |
|---|---|---|
| `OrderDTO` | orderId, totalPrice | OrderService (setOrderId, setTotalPrice) |
| `UserDTO` | userName, email, age | OrderService (getUserName), UserService (getUserName, setEmail, setAge) |

---

## 前置条件

- 已安装 voyager：`pip install -e .`
- 已安装 jdtls 且在 PATH 中

---

## 步骤 1：重置示例项目

```bash
cd <voyager项目根目录>
python examples/reset.py shop-dto
```

删除 `examples/shop-dto/` 下所有旧文件（包括 `.voyager/` 状态），从 `examples/_sources/shop-dto/` 复制一份全新的原始代码。

---

## 步骤 2：扫描项目，构建语义图

```bash
cd examples/shop-dto
voyager scan .
```

**预期输出：**
- 找到 4 个 Java 类（OrderDTO, OrderService, UserDTO, UserService）
- 24 个 symbol，5 个 reference
- 保存到 `.voyager/graph.json`

**说明：** 扫描阶段会尝试 LSP（jdtls），如果 LSP 结果不完整则回退到静态解析器。两种方式都能正确识别符号和引用。

---

## 步骤 3：规划重命名操作

```bash
voyager plan rename UserDTO.userName customerName
```

**预期输出：**

```
Plan valid. 1 file(s) affected:
  - src/main/java/com/shop/UserDTO.java
```

**说明：** `plan` 阶段只基于静态解析的引用预估受影响文件。静态解析器目前只能识别同文件内的引用，所以显示 1 个文件。实际执行时 LSP 会找到所有跨文件引用。

---

## 步骤 4：执行重命名

```bash
voyager apply -y
```

**预期输出：**

```
Operation applied successfully.
  Modified: src\main\java\com\shop\OrderService.java
  Modified: src\main\java\com\shop\UserDTO.java
  Modified: src\main\java\com\shop\UserService.java
```

**说明：** `-y` 跳过确认提示。LSP 驱动的 rename 会找到所有语义上正确的引用位置，跨 3 个文件修改。

---

## 步骤 5：验证结果

检查 3 个被修改的文件：

**UserDTO.java** — 字段和方法名都已改变：
- `private String customerName;`
- `getCustomerName()` / `setCustomerName()`

**OrderService.java** — 调用处已改变：
- `buyer.getCustomerName()`（原 `getUserName()`）

**UserService.java** — 调用处已改变：
- `user.getCustomerName()`（原 `getUserName()`）

**已知小问题：** setter 参数名仍为 `String userName`，这是 jdtls LSP rename 的默认行为（只重命名字段和 getter/setter 方法名，不改变 setter 参数名）。

---

## 步骤 6：重置以备下次测试

```bash
cd <voyager项目根目录>
python examples/reset.py shop-dto
```

每次测试结束后务必重置，否则后续测试会在已修改的文件上运行，导致符号找不到等问题。
