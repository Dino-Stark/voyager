"""Run Voyager V1 patch-first end-to-end example flows."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


EXAMPLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLES_DIR.parent
SRC_DIR = REPO_ROOT / "src"


@dataclass(frozen=True)
class CommandResult:
    """
    Captured subprocess output for a CLI command.
    """

    args: list[str]
    stdout: str
    stderr: str


def main() -> None:
    """
    Run the complete V1 example regression suite.
    """
    reset("shop-dto")
    try:
        run_shop_dto_patch_set_flow()
        run_shop_dto_file_lifecycle_patch_flow()
        run_multi_project_patch_isolation_flow()
    finally:
        stop_if_running(EXAMPLES_DIR / "shop-dto")
        stop_if_running(EXAMPLES_DIR / "mini-customer")
        stop_if_running(EXAMPLES_DIR / "mini-order")

    print("examples/e2e_v1.py: all flows passed")


def run_shop_dto_patch_set_flow() -> None:
    """
    Verify ordered patch sets through scan/plan/apply.
    """
    reset("shop-dto")
    project = EXAMPLES_DIR / "shop-dto"
    patch_one = project / "agent-1.patch"
    patch_two = project / "agent-2.patch"
    patch_one.write_text(
        """--- a/src/main/java/com/shop/OrderDTO.java
+++ b/src/main/java/com/shop/OrderDTO.java
@@ -1,13 +1,13 @@
 package com.shop;
 
 public class OrderDTO {
-    private String orderId;
+    private String externalOrderId;
     private double totalPrice;
 
-    public String getOrderId() {
-        return orderId;
+    public String getExternalOrderId() {
+        return externalOrderId;
     }
 
-    public void setOrderId(String orderId) {
-        this.orderId = orderId;
+    public void setExternalOrderId(String externalOrderId) {
+        this.externalOrderId = externalOrderId;
     }
--- a/src/main/java/com/shop/OrderService.java
+++ b/src/main/java/com/shop/OrderService.java
@@ -5,7 +5,7 @@ public class OrderService {
     private UserService userService = new UserService();
 
     public void createOrder(OrderDTO order, UserDTO user) {
-        order.setOrderId("ORD-001");
+        order.setExternalOrderId("ORD-001");
         order.setTotalPrice(99.9);
         this.buyer = user;
         String buyerName = buyer.getUserName();
""",
        encoding="utf-8",
    )
    patch_two.write_text(
        """--- a/src/main/java/com/shop/OrderDTO.java
+++ b/src/main/java/com/shop/OrderDTO.java
@@ -1,13 +1,13 @@
 package com.shop;
 
 public class OrderDTO {
-    private String externalOrderId;
+    private String agentOrderId;
     private double totalPrice;
 
-    public String getExternalOrderId() {
-        return externalOrderId;
+    public String getAgentOrderId() {
+        return agentOrderId;
     }
 
-    public void setExternalOrderId(String externalOrderId) {
-        this.externalOrderId = externalOrderId;
+    public void setAgentOrderId(String agentOrderId) {
+        this.agentOrderId = agentOrderId;
     }
--- a/src/main/java/com/shop/OrderService.java
+++ b/src/main/java/com/shop/OrderService.java
@@ -5,7 +5,7 @@ public class OrderService {
     private UserService userService = new UserService();
 
     public void createOrder(OrderDTO order, UserDTO user) {
-        order.setExternalOrderId("ORD-001");
+        order.setAgentOrderId("ORD-001");
         order.setTotalPrice(99.9);
         this.buyer = user;
         String buyerName = buyer.getUserName();
""",
        encoding="utf-8",
    )

    try:
        run_cli(project, "scan", ".")
        run_cli(project, "plan", "patch", str(patch_one), str(patch_two))
        run_cli(project, "apply", "-y")
        order_dto = read(project / "src/main/java/com/shop/OrderDTO.java")
        order_service = read(project / "src/main/java/com/shop/OrderService.java")
        assert_contains(order_dto, "private String agentOrderId;")
        assert_contains(order_dto, "getAgentOrderId()")
        assert_contains(order_dto, "setAgentOrderId")
        assert_contains(order_service, "setAgentOrderId")
        assert_not_contains(order_dto, "private String externalOrderId;")
        assert_not_contains(order_dto, "private String orderId;")
    finally:
        patch_one.unlink(missing_ok=True)
        patch_two.unlink(missing_ok=True)
        run_cli(project, "stop")


def run_shop_dto_file_lifecycle_patch_flow() -> None:
    """
    Verify create, modify, move, and delete file changes through VFS patches.
    """
    reset("shop-dto")
    project = EXAMPLES_DIR / "shop-dto"
    obsolete_file = project / "src/main/java/com/shop/ObsoleteDTO.java"
    obsolete_file.write_text(
        """package com.shop;

public class ObsoleteDTO {
    private String legacyId;
}
""",
        encoding="utf-8",
    )
    patch_path = project / "file-lifecycle.patch"
    patch_path.write_text(
        """--- /dev/null
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
similarity index 100%
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
""",
        encoding="utf-8",
    )

    try:
        run_cli(project, "scan", ".")
        run_cli(project, "plan", "patch", str(patch_path))
        run_cli(project, "apply", "-y")
        assert_contains(
            read(project / "src/main/java/com/shop/PatchOnlyDTO.java"),
            "private String externalId;",
        )
        assert not (project / "src/main/java/com/shop/UserDTOAudit.java").exists()
        journal = project / "src/main/java/com/shop/UserDTOJournal.java"
        assert journal.exists(), f"Expected {journal} to exist"
        assert_contains(read(journal), "public class UserDTOJournal")
        assert not obsolete_file.exists()
    finally:
        patch_path.unlink(missing_ok=True)
        run_cli(project, "stop")


def run_multi_project_patch_isolation_flow() -> None:
    """
    Verify separate example projects use separate Server processes for patch flows.
    """
    reset("mini-customer")
    reset("mini-order")
    customer = EXAMPLES_DIR / "mini-customer"
    order = EXAMPLES_DIR / "mini-order"
    customer_patch = customer / "customer.patch"
    order_patch = order / "order.patch"

    customer_patch.write_text(
        """--- a/src/main/java/com/example/customer/CustomerDTO.java
+++ b/src/main/java/com/example/customer/CustomerDTO.java
@@ -1,12 +1,12 @@
 package com.example.customer;
 
 public class CustomerDTO {
-    private String userName;
+    private String customerName;
 
-    public String getUserName() {
-        return userName;
+    public String getCustomerName() {
+        return customerName;
     }
 
-    public void setUserName(String userName) {
-        this.userName = userName;
+    public void setCustomerName(String customerName) {
+        this.customerName = customerName;
     }
--- a/src/main/java/com/example/customer/CustomerService.java
+++ b/src/main/java/com/example/customer/CustomerService.java
@@ -2,6 +2,6 @@ package com.example.customer;
 
 public class CustomerService {
     public String label(CustomerDTO customer) {
-        return customer.getUserName();
+        return customer.getCustomerName();
     }
 }
""",
        encoding="utf-8",
    )
    order_patch.write_text(
        """--- a/src/main/java/com/example/order/OrderDTO.java
+++ b/src/main/java/com/example/order/OrderDTO.java
@@ -1,12 +1,12 @@
 package com.example.order;
 
 public class OrderDTO {
-    private String orderCode;
+    private String externalCode;
 
-    public String getOrderCode() {
-        return orderCode;
+    public String getExternalCode() {
+        return externalCode;
     }
 
-    public void setOrderCode(String orderCode) {
-        this.orderCode = orderCode;
+    public void setExternalCode(String externalCode) {
+        this.externalCode = externalCode;
     }
--- a/src/main/java/com/example/order/OrderService.java
+++ b/src/main/java/com/example/order/OrderService.java
@@ -2,6 +2,6 @@ package com.example.order;
 
 public class OrderService {
     public String format(OrderDTO order) {
-        return order.getOrderCode();
+        return order.getExternalCode();
     }
 }
""",
        encoding="utf-8",
    )

    try:
        run_cli(customer, "start", ".")
        run_cli(order, "start", ".")
        customer_state = load_server_state(customer)
        order_state = load_server_state(order)
        assert customer_state["pid"] != order_state["pid"]
        assert customer_state["port"] != order_state["port"]
        assert customer_state["token"] != order_state["token"]

        run_cli(customer, "scan", ".")
        run_cli(customer, "plan", "patch", str(customer_patch))
        run_cli(customer, "apply", "-y")
        assert_contains(
            read(customer / "src/main/java/com/example/customer/CustomerDTO.java"),
            "customerName",
        )
        assert_contains(
            read(customer / "src/main/java/com/example/customer/CustomerService.java"),
            "getCustomerName",
        )

        run_cli(order, "scan", ".")
        run_cli(order, "plan", "patch", str(order_patch))
        run_cli(order, "apply", "-y")
        assert_contains(
            read(order / "src/main/java/com/example/order/OrderDTO.java"),
            "externalCode",
        )
        assert_contains(
            read(order / "src/main/java/com/example/order/OrderService.java"),
            "getExternalCode",
        )

        run_cli(customer, "stop")
        assert not (customer / ".voyager/cache/server.json").exists()
        assert (order / ".voyager/cache/server.json").exists()
        run_cli(order, "stop")
    finally:
        customer_patch.unlink(missing_ok=True)
        order_patch.unlink(missing_ok=True)


def reset(project_name: str) -> None:
    """
    Reset one example project from its gold-master source.
    """
    subprocess.run(
        [sys.executable, str(EXAMPLES_DIR / "reset.py"), project_name],
        cwd=REPO_ROOT,
        check=True,
        text=True,
    )


def run_cli(project: Path, *args: str) -> CommandResult:
    """
    Run the Voyager CLI module inside an example project.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = _pythonpath(env)
    cmd = [sys.executable, "-m", "voyager_cmd.main", *args]
    completed = subprocess.run(
        cmd,
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Command failed in {project}: {' '.join(cmd)}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return CommandResult(args=cmd, stdout=completed.stdout, stderr=completed.stderr)


def stop_if_running(project: Path) -> None:
    """
    Best-effort cleanup for a running example Server.
    """
    if not (project / ".voyager/cache/server.json").exists():
        return
    try:
        run_cli(project, "stop")
    except Exception:
        return


def load_server_state(project: Path) -> dict:
    """
    Load a project's Server discovery file.
    """
    return json.loads((project / ".voyager/cache/server.json").read_text(encoding="utf-8"))


def assert_contains(text: str, needle: str) -> None:
    """
    Assert a snippet exists in text with a useful failure message.
    """
    assert needle in text, f"Expected to find {needle!r}"


def assert_not_contains(text: str, needle: str) -> None:
    """
    Assert a snippet does not exist in text with a useful failure message.
    """
    assert needle not in text, f"Did not expect to find {needle!r}"


def read(path: Path) -> str:
    """
    Read a UTF-8 text file.
    """
    return path.read_text(encoding="utf-8")


def _pythonpath(env: dict[str, str]) -> str:
    """
    Build a PYTHONPATH that imports the repository source tree first.
    """
    existing = env.get("PYTHONPATH")
    if not existing:
        return str(SRC_DIR)
    return os.pathsep.join([str(SRC_DIR), existing])


if __name__ == "__main__":
    main()
