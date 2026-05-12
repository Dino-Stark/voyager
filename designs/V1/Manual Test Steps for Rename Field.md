# Manual Test Steps: shop-dto V1 Patch Flow

## Goal

Verify that Voyager can apply ordered unified diff patch sets through the current
Server-based runtime. The same patch operation covers source edits, creates,
deletes, moves, and multi-file changes.

Supported public operation in this flow:

- `patch`: apply one or more unified diff files in order.

Each scenario starts from a fresh reset so expected files are independent.

---

## Preconditions

- Voyager is installed in editable mode:

```bash
pip install -e .
```

- Java/JDK is available.
- JDT LS is available as `jdtls` on `PATH` for LSP snapshot validation.

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
- It verifies ordered patch sets, file create/modify/move/delete lifecycle, and
  multi-project Server isolation.
- It stops any Servers it starts.

---

## Scenario A: Ordered Patch Set

### Step A1: Reset

```bash
python examples/reset.py shop-dto
cd examples/shop-dto
```

### Step A2: Create Patch Files

```bash
cat > agent-1.patch <<'PATCH'
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

cat > agent-2.patch <<'PATCH'
--- a/src/main/java/com/shop/OrderDTO.java
+++ b/src/main/java/com/shop/OrderDTO.java
@@ -1,7 +1,7 @@
 package com.shop;
 
 public class OrderDTO {
-    private String externalOrderId;
+    private String agentOrderId;
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
voyager plan patch agent-1.patch agent-2.patch
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

- `OrderDTO.java` contains `private String agentOrderId;`.
- `private String externalOrderId;` was only an intermediate virtual state.
- The operation is rejected instead if any hunk context does not match.

### Step A6: Stop Server

```bash
voyager stop
```

---

## Scenario B: File Lifecycle Patch

### Step B1: Reset

```bash
cd ../../
python examples/reset.py shop-dto
cd examples/shop-dto
```

### Step B2: Add A Disposable Source File

This file gives the delete part of the patch a real on-disk target:

```bash
cat > src/main/java/com/shop/ObsoleteDTO.java <<'JAVA'
package com.shop;

public class ObsoleteDTO {
    private String legacyId;
}
JAVA
```

### Step B3: Create Lifecycle Patch

```bash
cat > file-lifecycle.patch <<'PATCH'
--- /dev/null
+++ b/src/main/java/com/shop/PatchOnlyDTO.java
@@ -0,0 +1,5 @@
+package com.shop;
+
+public class PatchOnlyDTO {
+    private String id;
+}
--- a/src/main/java/com/shop/PatchOnlyDTO.java
+++ b/src/main/java/com/shop/PatchOnlyDTO.java
@@ -1,5 +1,5 @@
 package com.shop;
 
 public class PatchOnlyDTO {
-    private String id;
+    private String externalId;
 }
diff --git a/src/main/java/com/shop/UserDTOAudit.java b/src/main/java/com/shop/UserDTOJournal.java
similarity index 80%
rename from src/main/java/com/shop/UserDTOAudit.java
rename to src/main/java/com/shop/UserDTOJournal.java
--- a/src/main/java/com/shop/UserDTOAudit.java
+++ b/src/main/java/com/shop/UserDTOJournal.java
@@ -1,6 +1,6 @@
 package com.shop;
 
-public class UserDTOAudit {
+public class UserDTOJournal {
     private UserDTO user;
 
-    public UserDTOAudit(UserDTO user) {
+    public UserDTOJournal(UserDTO user) {
--- a/src/main/java/com/shop/ObsoleteDTO.java
+++ /dev/null
@@ -1,5 +0,0 @@
-package com.shop;
-
-public class ObsoleteDTO {
-    private String legacyId;
-}
PATCH
```

### Step B4: Plan And Apply

```bash
voyager -v scan .
voyager plan patch file-lifecycle.patch
voyager apply -y
```

Expected:

- `PatchOnlyDTO.java` exists and contains `private String externalId;`.
- `UserDTOAudit.java` no longer exists.
- `UserDTOJournal.java` exists and declares `public class UserDTOJournal`.
- `ObsoleteDTO.java` no longer exists.

### Step B5: Stop Server

```bash
voyager stop
```

---

## Multi-Project Isolation Smoke Test

This verifies the V1 process model: one project root maps to one Voyager Server
process. Multiple sessions in the same project reuse a Server, while different
projects use independent Servers.

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

Then run patch flows in both projects.

`mini-customer`:

```bash
cat > customer.patch <<'PATCH'
--- a/src/main/java/com/example/customer/CustomerDTO.java
+++ b/src/main/java/com/example/customer/CustomerDTO.java
@@ -1,7 +1,7 @@
 package com.example.customer;
 
 public class CustomerDTO {
-    private String userName;
+    private String customerName;
 
     public String getUserName() {
         return userName;
PATCH

voyager -v scan .
voyager plan patch customer.patch
voyager apply -y
```

`mini-order`:

```bash
cat > order.patch <<'PATCH'
--- a/src/main/java/com/example/order/OrderDTO.java
+++ b/src/main/java/com/example/order/OrderDTO.java
@@ -1,7 +1,7 @@
 package com.example.order;
 
 public class OrderDTO {
-    private String orderCode;
+    private String externalCode;
 
     public String getOrderCode() {
         return orderCode;
PATCH

voyager -v scan .
voyager plan patch order.patch
voyager apply -y
```

Expected:

- `mini-customer` modifies only the customer project.
- `mini-order` modifies only the order project.
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

---

## Reset For Next Run

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
```

This restores the example project to its original state and removes runtime
`.voyager/` state.
