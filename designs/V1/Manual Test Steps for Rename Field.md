# Manual Test Steps: shop-dto V1 operations

## Goal

Verify that Voyager can apply unified diff patches, add/remove Java fields, and rename Java fields, methods, and classes across the example project through the current Server-based runtime.

Supported operations in this flow:

- `patch`: apply a unified diff produced by a CLI-first coding agent
- `add_field`: add `com.shop.OrderDTO.giftMessage`
- `remove_field`: remove `com.shop.OrderDTO.giftMessage`
- `rename_field`: `com.shop.UserDTO.userName` -> `customerName`
- `rename_method`: `com.shop.UserService.formatDisplayName` -> `formatCustomerLabel`
- `rename_class`: `com.shop.UserDTO` -> `CustomerProfile`

Each scenario starts from a fresh reset so the expected files are independent.

---

## Preconditions

- Voyager is installed in editable mode:

```bash
pip install -e .
```

- JDT LS is available as `jdtls` on `PATH`.
- Java/JDK is available for JDT LS.

Optional check:

```bash
python -m scripts.setup_jdtls --check
```

---

## Automated E2E Regression

From the Voyager repository root:

```bash
python examples/e2e_v1.py
```

Expected:

- The script resets example projects before each scenario.
- It verifies `patch`, `add_field`, `remove_field`, `rename_field`, `rename_method`,
  `rename_class`, and multi-project Server isolation.
- It stops any Servers it starts.

## Scenario A: patch

### Step A1: Reset

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
```

### Step A2: Create Patch File

```bash
cd examples/shop-dto
cat > agent.patch <<'PATCH'
--- a/src/main/java/com/shop/OrderDTO.java
+++ b/src/main/java/com/shop/OrderDTO.java
@@ -1,7 +1,7 @@
 package com.shop;
 
 public class OrderDTO {
-    private String orderId;
+    private String externalOrderId;
     private double totalPrice;
 
     public String getOrderId() {
PATCH
```

### Step A3: Scan

```bash
voyager -v scan .
```

### Step A4: Plan Patch

```bash
voyager plan patch agent.patch
```

Expected:

```text
Plan valid. 1 file(s) affected:
  - src/main/java/com/shop/OrderDTO.java
```

### Step A5: Apply Patch

```bash
voyager apply -y
```

Expected:

- `OrderDTO.java` contains `private String externalOrderId;`.
- The operation is rejected instead if hunk context does not match.

### Step A6: Stop Server

```bash
voyager stop
```

---

## Scenario B: add_field and remove_field

### Step B1: Reset

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
```

### Step B2: Scan

```bash
cd examples/shop-dto
voyager -v scan .
```

### Step B3: Plan Field Add

```bash
voyager plan add_field com.shop.OrderDTO giftMessage String
```

Expected:

```text
Plan valid. 1 file(s) affected:
  - src/main/java/com/shop/OrderDTO.java
```

### Step B4: Apply Field Add

```bash
voyager apply -y
```

Expected:

- `OrderDTO.java` contains `private String giftMessage;`.
- `OrderDTO.java` contains `getGiftMessage()` and `setGiftMessage(String giftMessage)`.

### Step B5: Plan Field Remove

```bash
voyager plan remove_field com.shop.OrderDTO giftMessage
```

Expected:

```text
Plan valid. 1 file(s) affected:
  - src/main/java/com/shop/OrderDTO.java
```

### Step B6: Apply Field Remove

```bash
voyager apply -y
```

Expected:

- `giftMessage`, `getGiftMessage()`, and `setGiftMessage(...)` are removed from `OrderDTO.java`.
- The operation is rejected instead if Voyager detects external typed field or accessor references.

### Step B7: Stop Server

```bash
voyager stop
```

---

## Scenario C: rename_field

### Step C1: Reset

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
```

### Step C2: Start Server

```bash
cd examples/shop-dto
voyager -v start .
```

Expected:

- Voyager starts a project Server in the background.
- JDT LS starts once inside that Server.
- Server connection info is written to `.voyager/cache/server.json`.
- No semantic graph is built yet.

### Step C3: Scan

```bash
voyager -v scan .
```

Expected:

- Voyager reuses the project Server.
- 5 Java classes are detected:
  - `OrderDTO`
  - `OrderService`
  - `UserDTO`
  - `UserDTOAudit`
  - `UserService`
- References are saved to `.voyager/graph.json`.

### Step C4: Plan Field Rename

```bash
voyager plan rename_field com.shop.UserDTO.userName customerName
```

Expected:

```text
Plan valid. 3 file(s) affected:
  - src/main/java/com/shop/OrderService.java
  - src/main/java/com/shop/UserDTO.java
  - src/main/java/com/shop/UserService.java
```

The plan includes JavaBean accessor call sites such as `getUserName()` because JDT LS field rename will update them to `getCustomerName()`.

### Step C5: Apply

```bash
voyager apply -y
```

Expected:

```text
Operation applied successfully.
  Modified: src\main\java\com\shop\OrderService.java
  Modified: src\main\java\com\shop\UserDTO.java
  Modified: src\main\java\com\shop\UserService.java
```

### Step C6: Verify Source Changes

`UserDTO.java`:

- `private String customerName;`
- `getCustomerName()`
- `setCustomerName(String userName)`
- `this.customerName = userName;`

`OrderService.java`:

- `buyer.getCustomerName()`

`UserService.java`:

- `user.getCustomerName()`

Known V1 behavior: the setter parameter can remain `String userName`. JDT LS renames the field symbol and JavaBean accessor names, but the parameter is a local variable.

### Step C7: Stop Server

```bash
voyager stop
```

Expected:

- Server shuts down.
- JDT LS shuts down through `LspClient.shutdown()`.
- `.voyager/cache/server.json` is removed.

---

## Scenario D: rename_method

### Step D1: Reset

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
cd examples/shop-dto
```

### Step D2: Scan

`scan/plan/apply` can auto-start the Server, so this scenario does not require an explicit `start`.

```bash
voyager -v scan .
```

### Step D3: Plan Method Rename

```bash
voyager plan rename_method com.shop.UserService.formatDisplayName formatCustomerLabel
```

Expected:

```text
Plan valid. 2 file(s) affected:
  - src/main/java/com/shop/OrderService.java
  - src/main/java/com/shop/UserService.java
```

The method declaration lives in `UserService.java`; the typed call site lives in `OrderService.java`:

```java
userService.formatDisplayName(user)
```

### Step D4: Apply

```bash
voyager apply -y
```

Expected:

```text
Operation applied successfully.
  Modified: src\main\java\com\shop\OrderService.java
  Modified: src\main\java\com\shop\UserService.java
```

### Step D5: Verify Source Changes

`UserService.java`:

- `public String formatCustomerLabel(UserDTO user)`

`OrderService.java`:

- `return userService.formatCustomerLabel(user);`

### Step D6: Stop Server

```bash
voyager stop
```

---

## Scenario E: rename_class

### Step E1: Reset

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
cd examples/shop-dto
```

### Step E2: Scan

```bash
voyager -v scan .
```

### Step E3: Plan Class Rename

```bash
voyager plan rename_class com.shop.UserDTO CustomerProfile
```

Expected:

```text
Plan valid. 4 file(s) affected:
  - src/main/java/com/shop/OrderService.java
  - src/main/java/com/shop/UserDTO.java
  - src/main/java/com/shop/UserDTOAudit.java
  - src/main/java/com/shop/UserService.java
```

### Step E4: Apply

```bash
voyager apply -y
```

Expected:

```text
Operation applied successfully.
  Modified: src\main\java\com\shop\OrderService.java
  Modified: src\main\java\com\shop\CustomerProfile.java
  Modified: src\main\java\com\shop\UserDTOAudit.java
  Modified: src\main\java\com\shop\UserService.java
```

`rename_class` uses JDT LS semantic rename and then moves the Java source file when the file name matches the old public class name.

### Step E5: Verify Source Changes

Expected:

- `src/main/java/com/shop/CustomerProfile.java` exists.
- `src/main/java/com/shop/UserDTO.java` no longer exists.
- `CustomerProfile.java` declares `public class CustomerProfile`.
- `OrderService.java`, `UserDTOAudit.java`, and `UserService.java` use `CustomerProfile`.

### Step E6: Stop Server

```bash
voyager stop
```

---

## Reset For Next Run

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
```

This restores the example project to its original state and removes runtime `.voyager/` state.

---

## Multi-Project Isolation Smoke Test

This verifies the V1 process model: one project root maps to one Voyager Server process. Multiple sessions in the same project should reuse a Server, while different projects should use independent Servers.

From the Voyager repository root:

```bash
python examples/reset.py mini-customer
python examples/reset.py mini-order
```

In two terminals, start both projects:

```bash
cd examples/mini-customer
voyager -v start .
```

```bash
cd examples/mini-order
voyager -v start .
```

Expected:

- The two commands report different Server pids.
- Each project has its own `.voyager/cache/server.json`.
- The two `server.json` files have different `pid`, `port`, `token`, and `project_path` values.

Then run both project flows:

```bash
cd examples/mini-customer
voyager -v scan .
voyager plan rename_field com.example.customer.CustomerDTO.userName customerName
voyager apply -y
```

```bash
cd examples/mini-order
voyager -v scan .
voyager plan rename_field com.example.order.OrderDTO.orderCode externalCode
voyager apply -y
```

Expected:

- `mini-customer` modifies only `CustomerDTO.java` and `CustomerService.java`.
- `mini-order` modifies only `OrderDTO.java` and `OrderService.java`.
- `voyager status` in both projects reports each project's own Server pid.

Stop one project:

```bash
cd examples/mini-customer
voyager stop
```

Expected:

- `mini-customer/.voyager/cache/server.json` is removed.
- `mini-order` still reports its own Server as running.

Then stop the other project and reset:

```bash
cd ../mini-order
voyager stop
cd ../..
python examples/reset.py mini-customer
python examples/reset.py mini-order
```

Expected:

- Both project-scoped Servers are stopped.
- Both project-local `.voyager/cache/server.json` files are removed.
