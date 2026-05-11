"""Run Voyager V1 end-to-end example flows.

The script exercises the public CLI path against the resettable example
projects. It is intentionally kept under examples/ because it verifies the
documented manual flows using the same fixtures users can inspect.
"""

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
        run_shop_dto_patch_flow()
        run_shop_dto_add_remove_field_flow()
        run_shop_dto_rename_field_flow()
        run_shop_dto_rename_method_flow()
        run_shop_dto_rename_class_flow()
        run_multi_project_isolation_flow()
    finally:
        stop_if_running(EXAMPLES_DIR / "shop-dto")
        stop_if_running(EXAMPLES_DIR / "mini-customer")
        stop_if_running(EXAMPLES_DIR / "mini-order")

    print("examples/e2e_v1.py: all flows passed")


def run_shop_dto_add_remove_field_flow() -> None:
    """
    Verify add_field and remove_field through scan/plan/apply.
    """
    reset("shop-dto")
    project = EXAMPLES_DIR / "shop-dto"

    run_cli(project, "scan", ".")
    run_cli(project, "plan", "add_field", "com.shop.OrderDTO", "giftMessage", "String")
    run_cli(project, "apply", "-y")
    order_dto = read(project / "src/main/java/com/shop/OrderDTO.java")
    assert_contains(order_dto, "private String giftMessage;")
    assert_contains(order_dto, "public String getGiftMessage()")
    assert_contains(order_dto, "public void setGiftMessage(String giftMessage)")

    run_cli(project, "plan", "remove_field", "com.shop.OrderDTO", "giftMessage")
    run_cli(project, "apply", "-y")
    order_dto = read(project / "src/main/java/com/shop/OrderDTO.java")
    assert_not_contains(order_dto, "giftMessage")
    assert_graph_has_field(project, "com.shop.OrderDTO.giftMessage", expected=False)
    run_cli(project, "stop")


def run_shop_dto_patch_flow() -> None:
    """
    Verify a coding-agent-style unified diff through scan/plan/apply.
    """
    reset("shop-dto")
    project = EXAMPLES_DIR / "shop-dto"
    patch_path = project / "agent.patch"
    patch_path.write_text(
        """--- a/src/main/java/com/shop/OrderDTO.java
+++ b/src/main/java/com/shop/OrderDTO.java
@@ -1,7 +1,7 @@
 package com.shop;
 
 public class OrderDTO {
-    private String orderId;
+    private String externalOrderId;
     private double totalPrice;
 
     public String getOrderId() {
""",
        encoding="utf-8",
    )

    try:
        run_cli(project, "scan", ".")
        run_cli(project, "plan", "patch", str(patch_path))
        run_cli(project, "apply", "-y")
        order_dto = read(project / "src/main/java/com/shop/OrderDTO.java")
        assert_contains(order_dto, "private String externalOrderId;")
        assert_not_contains(order_dto, "private String orderId;")
    finally:
        patch_path.unlink(missing_ok=True)
        run_cli(project, "stop")


def run_shop_dto_rename_field_flow() -> None:
    """
    Verify the documented rename_field example flow.
    """
    reset("shop-dto")
    project = EXAMPLES_DIR / "shop-dto"

    run_cli(project, "start", ".")
    run_cli(project, "scan", ".")
    run_cli(project, "plan", "rename_field", "com.shop.UserDTO.userName", "customerName")
    run_cli(project, "apply", "-y")

    user_dto = read(project / "src/main/java/com/shop/UserDTO.java")
    order_service = read(project / "src/main/java/com/shop/OrderService.java")
    user_service = read(project / "src/main/java/com/shop/UserService.java")
    assert_contains(user_dto, "private String customerName;")
    assert_contains(user_dto, "getCustomerName()")
    assert_contains(order_service, "buyer.getCustomerName()")
    assert_contains(user_service, "user.getCustomerName()")
    run_cli(project, "stop")


def run_shop_dto_rename_method_flow() -> None:
    """
    Verify the documented rename_method example flow.
    """
    reset("shop-dto")
    project = EXAMPLES_DIR / "shop-dto"

    run_cli(project, "scan", ".")
    run_cli(
        project,
        "plan",
        "rename_method",
        "com.shop.UserService.formatDisplayName",
        "formatCustomerLabel",
    )
    run_cli(project, "apply", "-y")

    user_service = read(project / "src/main/java/com/shop/UserService.java")
    order_service = read(project / "src/main/java/com/shop/OrderService.java")
    assert_contains(user_service, "formatCustomerLabel(UserDTO user)")
    assert_contains(order_service, "userService.formatCustomerLabel(user)")
    run_cli(project, "stop")


def run_shop_dto_rename_class_flow() -> None:
    """
    Verify the documented rename_class example flow.
    """
    reset("shop-dto")
    project = EXAMPLES_DIR / "shop-dto"

    run_cli(project, "scan", ".")
    run_cli(project, "plan", "rename_class", "com.shop.UserDTO", "CustomerProfile")
    run_cli(project, "apply", "-y")

    customer_profile = project / "src/main/java/com/shop/CustomerProfile.java"
    old_user_dto = project / "src/main/java/com/shop/UserDTO.java"
    assert customer_profile.exists(), f"Expected {customer_profile} to exist"
    assert not old_user_dto.exists(), f"Expected {old_user_dto} to be removed"
    assert_contains(read(customer_profile), "public class CustomerProfile")
    assert_contains(read(project / "src/main/java/com/shop/OrderService.java"), "CustomerProfile")
    assert_contains(read(project / "src/main/java/com/shop/UserDTOAudit.java"), "CustomerProfile")
    assert_contains(read(project / "src/main/java/com/shop/UserService.java"), "CustomerProfile")
    run_cli(project, "stop")


def run_multi_project_isolation_flow() -> None:
    """
    Verify separate example projects use separate Server processes.
    """
    reset("mini-customer")
    reset("mini-order")
    customer = EXAMPLES_DIR / "mini-customer"
    order = EXAMPLES_DIR / "mini-order"

    run_cli(customer, "start", ".")
    run_cli(order, "start", ".")
    customer_state = load_server_state(customer)
    order_state = load_server_state(order)
    assert customer_state["pid"] != order_state["pid"]
    assert customer_state["port"] != order_state["port"]
    assert customer_state["token"] != order_state["token"]

    run_cli(customer, "scan", ".")
    run_cli(
        customer,
        "plan",
        "rename_field",
        "com.example.customer.CustomerDTO.userName",
        "customerName",
    )
    run_cli(customer, "apply", "-y")
    assert_contains(
        read(customer / "src/main/java/com/example/customer/CustomerDTO.java"),
        "customerName",
    )

    run_cli(order, "scan", ".")
    run_cli(
        order,
        "plan",
        "rename_field",
        "com.example.order.OrderDTO.orderCode",
        "externalCode",
    )
    run_cli(order, "apply", "-y")
    assert_contains(
        read(order / "src/main/java/com/example/order/OrderDTO.java"),
        "externalCode",
    )

    run_cli(customer, "stop")
    assert not (customer / ".voyager/cache/server.json").exists()
    assert (order / ".voyager/cache/server.json").exists()
    run_cli(order, "stop")


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


def assert_graph_has_field(project: Path, symbol_id: str, *, expected: bool) -> None:
    """
    Assert whether a field symbol exists in the saved graph.
    """
    graph = json.loads((project / ".voyager/graph.json").read_text(encoding="utf-8"))
    ids = {symbol["id"] for symbol in graph.get("symbols", [])}
    assert (symbol_id in ids) is expected


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
