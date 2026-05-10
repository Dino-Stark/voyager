# Manual Test Steps: shop-dto rename_field

## Goal

Verify that Voyager can rename `UserDTO.userName` to `customerName` across the example Java project through the current Server-based runtime.

Expected modified files:

- `src/main/java/com/shop/OrderService.java`
- `src/main/java/com/shop/UserDTO.java`
- `src/main/java/com/shop/UserService.java`

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

## Step 1: Reset the Example Project

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
```

This removes `examples/shop-dto/` contents, including any old `.voyager/` state, then copies a fresh project from `examples/_sources/shop-dto/`.

---

## Step 2: Start Server

```bash
cd examples/shop-dto
voyager -v start .
```

Expected:

- Voyager starts a project Server in the background.
- JDT LS starts once inside that Server.
- Server connection info is written to `.voyager/cache/server.json`.
- No semantic graph is built yet; `start` only manages the Server lifecycle.

`scan/plan/apply` still auto-start a Server if one is not running, but this manual flow uses `start` explicitly so Server lifecycle and project analysis are tested separately.

---

## Step 3: Scan

```bash
voyager -v scan .
```

Expected:

- Voyager reuses the project Server started in Step 2.
- 4 Java classes are detected:
  - `OrderDTO`
  - `OrderService`
  - `UserDTO`
  - `UserService`
- 24 symbols are detected.
- References are saved to `.voyager/graph.json`.

Current expected reference count is `14`, because the graph now records typed method calls in addition to type/parameter/field references.

---

## Step 4: Plan Rename

```bash
voyager plan rename UserDTO.userName customerName
```

Expected:

```text
Plan valid. 3 file(s) affected:
  - src/main/java/com/shop/OrderService.java
  - src/main/java/com/shop/UserDTO.java
  - src/main/java/com/shop/UserService.java
```

The plan includes JavaBean accessor call sites such as `getUserName()` because JDT LS field rename will update them to `getCustomerName()`.

---

## Step 5: Apply

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

`-y` skips the confirmation prompt.

---

## Step 6: Verify Source Changes

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

---

## Step 7: Stop Server

```bash
voyager stop
```

Expected:

- Server shuts down.
- JDT LS shuts down through `LspClient.shutdown()`.
- `.voyager/cache/server.json` is removed.

---

## Step 8: Reset For Next Run

From the Voyager repository root:

```bash
python examples/reset.py shop-dto
```

This restores the example project to its original state and removes runtime `.voyager/` state.
