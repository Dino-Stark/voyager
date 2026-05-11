import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.engine.execution_engine import ExecutionEngine, _normalize_newlines, apply_lsp_edits
from core.diff.patch_engine import apply_parsed_patch, parse_unified_patch
from core.graph.builder import GraphBuilder
from core.lsp.client import LspPosition, LspRange, LspTextEdit, LspWorkspaceEdit, path_to_uri
from core.operation.models import (
    AddFieldOperation,
    PatchOperation,
    RemoveFieldOperation,
    RenameClassOperation,
    RenameFieldOperation,
    RenameMethodOperation,
)
from core.parser.java_parser import parse_java_project_static
from core.rules.validator import RuleValidator
from cli.commands.plan import _build_operation


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def java_project(tmp_path: Path) -> Path:
    root = tmp_path
    write(
        root / "src/main/java/com/acme/OrderDTO.java",
        """package com.acme;

public class OrderDTO {
    private String userId;
    private int amount;

    public String getUserId() {
        return userId;
    }
}
""",
    )
    write(
        root / "src/main/java/com/acme/OrderService.java",
        """package com.acme;

public class OrderService {
    public void create(OrderDTO order) {
        String id = order.userId;
    }
}
""",
    )
    return root


def test_static_parser_and_graph_find_dto_field_references(java_project: Path) -> None:
    classes = parse_java_project_static(java_project)
    graph = GraphBuilder(java_project).build(classes)

    field = graph.resolve_field("OrderDTO", "userId")
    assert field is not None
    assert field.id == "com.acme.OrderDTO.userId"

    affected = graph.get_affected_files_for_field("OrderDTO", "userId")
    assert "src/main/java/com/acme/OrderDTO.java" in affected
    assert "src/main/java/com/acme/OrderService.java" in affected


def test_plan_includes_java_bean_accessor_call_sites(java_project: Path) -> None:
    service = java_project / "src/main/java/com/acme/OrderService.java"
    service.write_text(
        """package com.acme;

public class OrderService {
    public void create(OrderDTO order) {
        String id = order.getUserId();
    }
}
""",
        encoding="utf-8",
    )

    graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    affected = graph.get_affected_files_for_field("OrderDTO", "userId")

    assert affected == [
        "src/main/java/com/acme/OrderDTO.java",
        "src/main/java/com/acme/OrderService.java",
    ]


def test_plan_method_rename_includes_typed_call_sites(java_project: Path) -> None:
    dto = java_project / "src/main/java/com/acme/OrderDTO.java"
    dto.write_text(
        """package com.acme;

public class OrderDTO {
    private String userId;

    public String label() {
        return userId;
    }
}
""",
        encoding="utf-8",
    )
    service = java_project / "src/main/java/com/acme/OrderService.java"
    service.write_text(
        """package com.acme;

public class OrderService {
    public void create(OrderDTO order) {
        String label = order.label();
    }
}
""",
        encoding="utf-8",
    )

    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    result = engine.plan(RenameMethodOperation(target="com.acme.OrderDTO.label", to="displayLabel"))

    assert result.is_valid
    assert result.affected_files == [
        "src/main/java/com/acme/OrderDTO.java",
        "src/main/java/com/acme/OrderService.java",
    ]


def test_plan_class_rename_includes_type_references(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    result = engine.plan(RenameClassOperation(target="com.acme.OrderDTO", to="PurchaseDTO"))

    assert result.is_valid
    assert result.affected_files == [
        "src/main/java/com/acme/OrderDTO.java",
        "src/main/java/com/acme/OrderService.java",
    ]


def test_plan_rejects_overloaded_method_rename(java_project: Path) -> None:
    dto = java_project / "src/main/java/com/acme/OrderDTO.java"
    dto.write_text(
        """package com.acme;

public class OrderDTO {
    public String label() {
        return "";
    }

    public String label(String prefix) {
        return prefix;
    }
}
""",
        encoding="utf-8",
    )

    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    result = engine.plan(RenameMethodOperation(target="com.acme.OrderDTO.label", to="displayLabel"))

    assert not result.is_valid
    assert result.violations[0]["type"] == "ambiguous_symbol"


def test_cli_builds_explicit_rename_operations() -> None:
    field_op = _build_operation("rename_field", "com.shop.UserDTO.userName", "customerName")
    method_op = _build_operation(
        "rename_method", "com.shop.UserService.formatDisplayName", "formatCustomerLabel"
    )
    class_op = _build_operation("rename_class", "com.shop.UserDTO", "CustomerProfile")
    prefixed_field_op = _build_operation(
        "rename", "field:com.shop.UserDTO.userName", "customerName"
    )
    prefixed_method_op = _build_operation(
        "rename", "method:com.shop.UserService.formatDisplayName", "formatCustomerLabel"
    )
    prefixed_class_op = _build_operation("rename", "class:com.shop.UserDTO", "CustomerProfile")

    assert isinstance(field_op, RenameFieldOperation)
    assert isinstance(method_op, RenameMethodOperation)
    assert isinstance(class_op, RenameClassOperation)
    assert isinstance(prefixed_field_op, RenameFieldOperation)
    assert isinstance(prefixed_method_op, RenameMethodOperation)
    assert isinstance(prefixed_class_op, RenameClassOperation)


def test_cli_builds_add_and_remove_field_operations() -> None:
    add_op = _build_operation("add_field", "com.shop.OrderDTO", "giftMessage", ["String"])
    add_with_default_op = _build_operation(
        "add_field", "com.shop.OrderDTO", "active", ["boolean", "true"]
    )
    remove_op = _build_operation("remove_field", "com.shop.OrderDTO", "giftMessage")
    remove_fqn_op = _build_operation("remove_field", "com.shop.OrderDTO.giftMessage", None)

    assert isinstance(add_op, AddFieldOperation)
    assert add_op.target == "com.shop.OrderDTO"
    assert add_op.field_name == "giftMessage"
    assert add_op.field_type == "String"
    assert isinstance(add_with_default_op, AddFieldOperation)
    assert add_with_default_op.field_type == "boolean"
    assert add_with_default_op.default_value == "true"
    assert isinstance(remove_op, RemoveFieldOperation)
    assert remove_op.target == "com.shop.OrderDTO"
    assert remove_op.field_name == "giftMessage"
    assert isinstance(remove_fqn_op, RemoveFieldOperation)
    assert remove_fqn_op.target == "com.shop.OrderDTO"
    assert remove_fqn_op.field_name == "giftMessage"


def test_cli_builds_patch_operation(tmp_path: Path) -> None:
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(
        """--- a/src/main/java/com/shop/OrderDTO.java
+++ b/src/main/java/com/shop/OrderDTO.java
@@ -1,2 +1,2 @@
 package com.shop;
-class OldName {}
+class NewName {}
""",
        encoding="utf-8",
    )

    operation = _build_operation("patch", str(patch_file), None)

    assert isinstance(operation, PatchOperation)
    assert operation.description == str(patch_file)
    assert "class NewName" in operation.patch


def test_cli_rejects_legacy_unprefixed_rename() -> None:
    with pytest.raises(ValueError, match="requires a target prefix"):
        _build_operation("rename", "UserDTO.userName", "customerName")


def test_rename_operations_require_fqn_targets() -> None:
    with pytest.raises(ValueError, match="fully qualified"):
        RenameFieldOperation(target="OrderDTO.userId", to="customerId")
    with pytest.raises(ValueError, match="fully qualified"):
        RenameMethodOperation(target="OrderDTO.getUserId", to="getCustomerId")
    with pytest.raises(ValueError, match="fully qualified"):
        RenameClassOperation(target="OrderDTO", to="PurchaseDTO")


def test_add_remove_operations_require_fqn_targets() -> None:
    with pytest.raises(ValueError, match="fully qualified"):
        AddFieldOperation(target="OrderDTO", field_name="giftMessage")
    with pytest.raises(ValueError, match="fully qualified"):
        RemoveFieldOperation(target="OrderDTO", field_name="giftMessage")


def test_parse_and_apply_unified_patch() -> None:
    patch_files = parse_unified_patch(
        """--- a/OrderDTO.java
+++ b/OrderDTO.java
@@ -1,3 +1,3 @@
 class OrderDTO {
-    private String orderId;
+    private String externalId;
 }
"""
    )

    modified = apply_parsed_patch(
        "class OrderDTO {\n    private String orderId;\n}\n",
        patch_files[0],
    )

    assert patch_files[0].target_path == "OrderDTO.java"
    assert "externalId" in modified
    assert "orderId" not in modified


def test_plan_accepts_fqn_field_target(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    engine.graph = graph

    result = engine.plan(RenameFieldOperation(target="com.acme.OrderDTO.userId", to="customerId"))

    assert result.is_valid
    assert result.violations == []
    assert len(result.affected_files) == 2


def test_apply_lsp_edits_uses_reverse_order() -> None:
    content = "private String userId;\nreturn userId;\n"
    edits = [
        LspTextEdit(
            range=LspRange(LspPosition(0, 15), LspPosition(0, 21)),
            new_text="customerId",
        ),
        LspTextEdit(
            range=LspRange(LspPosition(1, 7), LspPosition(1, 13)),
            new_text="customerId",
        ),
    ]

    assert apply_lsp_edits(content, edits) == "private String customerId;\nreturn customerId;\n"


def test_normalize_newlines_preserves_original_style() -> None:
    assert _normalize_newlines("a\r\nb\rc\n", "x\ny\n") == "a\nb\nc\n"
    assert _normalize_newlines("a\nb\r\nc\n", "x\r\ny\r\n") == "a\r\nb\r\nc\r\n"


def test_apply_refuses_rename_without_lsp(monkeypatch: pytest.MonkeyPatch, java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    engine.graph = graph

    class FakeConfig:
        command = ["jdtls"]

        def find_server_command(self):
            return None

    monkeypatch.setattr("core.engine.execution_engine.get_language_config", lambda language: FakeConfig())

    result = engine.apply(RenameFieldOperation(target="com.acme.OrderDTO.userId", to="customerId"))

    assert not result.success
    assert result.errors[0]["type"] == "lsp_unavailable"


def test_apply_refuses_method_rename_without_lsp(
    monkeypatch: pytest.MonkeyPatch, java_project: Path
) -> None:
    engine = ExecutionEngine(java_project)
    graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    engine.graph = graph

    class FakeConfig:
        command = ["jdtls"]

        def find_server_command(self):
            return None

    monkeypatch.setattr("core.engine.execution_engine.get_language_config", lambda language: FakeConfig())

    result = engine.apply(
        RenameMethodOperation(target="com.acme.OrderDTO.getUserId", to="getCustomerId")
    )

    assert not result.success
    assert result.errors[0]["type"] == "lsp_unavailable"


def test_apply_refuses_class_rename_without_lsp(
    monkeypatch: pytest.MonkeyPatch, java_project: Path
) -> None:
    engine = ExecutionEngine(java_project)
    graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    engine.graph = graph

    class FakeConfig:
        command = ["jdtls"]

        def find_server_command(self):
            return None

    monkeypatch.setattr("core.engine.execution_engine.get_language_config", lambda language: FakeConfig())

    result = engine.apply(RenameClassOperation(target="com.acme.OrderDTO", to="PurchaseDTO"))

    assert not result.success
    assert result.errors[0]["type"] == "lsp_unavailable"


def test_class_rename_moves_java_file_after_lsp_edit(
    monkeypatch: pytest.MonkeyPatch, java_project: Path
) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    class FakeConfig:
        command = ["jdtls"]

        def find_server_command(self):
            return ["jdtls"]

    async def fake_request_lsp_rename(source_path, symbol, operation, client=None):
        return LspWorkspaceEdit(
            changes={
                path_to_uri(source_path): [
                    LspTextEdit(
                        range=LspRange(
                            LspPosition(symbol.line - 1, symbol.column - 1),
                            LspPosition(symbol.line - 1, symbol.column - 1 + len(symbol.name)),
                        ),
                        new_text=operation.to,
                    )
                ]
            }
        )

    monkeypatch.setattr("core.engine.execution_engine.get_language_config", lambda language: FakeConfig())
    monkeypatch.setattr(engine, "_request_lsp_rename", fake_request_lsp_rename)

    result = engine.apply(RenameClassOperation(target="com.acme.OrderDTO", to="PurchaseDTO"))

    assert result.success
    assert "src/main/java/com/acme/PurchaseDTO.java" in [
        Path(path).as_posix() for path in result.modified_files
    ]
    assert not (java_project / "src/main/java/com/acme/OrderDTO.java").exists()
    assert "class PurchaseDTO" in (
        java_project / "src/main/java/com/acme/PurchaseDTO.java"
    ).read_text(encoding="utf-8")


def test_add_field_applies_static_source_patch(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    result = engine.apply(
        AddFieldOperation(
            target="com.acme.OrderDTO",
            field_name="trackingCode",
            field_type="String",
        )
    )

    source = (java_project / "src/main/java/com/acme/OrderDTO.java").read_text(
        encoding="utf-8"
    )
    assert result.success
    assert [Path(path).as_posix() for path in result.modified_files] == [
        "src/main/java/com/acme/OrderDTO.java"
    ]
    assert "private String trackingCode;" in source
    assert "public String getTrackingCode()" in source
    assert "public void setTrackingCode(String trackingCode)" in source
    assert engine.graph is not None
    assert engine.graph.resolve_field("com.acme.OrderDTO", "trackingCode") is not None


def test_add_boolean_field_uses_is_getter(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    result = engine.apply(
        AddFieldOperation(
            target="com.acme.OrderDTO",
            field_name="active",
            field_type="boolean",
            default_value="true",
        )
    )

    source = (java_project / "src/main/java/com/acme/OrderDTO.java").read_text(
        encoding="utf-8"
    )
    assert result.success
    assert "private boolean active = true;" in source
    assert "public boolean isActive()" in source


def test_add_field_plan_rejects_accessor_conflict(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    result = engine.plan(
        AddFieldOperation(
            target="com.acme.OrderDTO",
            field_name="userId",
            field_type="String",
        )
    )

    assert not result.is_valid
    assert result.violations[0]["type"] == "symbol_already_exists"


def test_remove_field_applies_static_source_patch(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    add_result = engine.apply(
        AddFieldOperation(
            target="com.acme.OrderDTO",
            field_name="trackingCode",
            field_type="String",
        )
    )
    remove_result = engine.apply(
        RemoveFieldOperation(target="com.acme.OrderDTO", field_name="trackingCode")
    )

    source = (java_project / "src/main/java/com/acme/OrderDTO.java").read_text(
        encoding="utf-8"
    )
    assert add_result.success
    assert remove_result.success
    assert "trackingCode" not in source
    assert engine.graph is not None
    assert engine.graph.resolve_field("com.acme.OrderDTO", "trackingCode") is None


def test_remove_field_plan_rejects_external_references(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    result = engine.plan(
        RemoveFieldOperation(target="com.acme.OrderDTO", field_name="userId")
    )

    assert not result.is_valid
    assert result.violations[0]["type"] == "validation_failed"


def test_patch_operation_applies_unified_diff(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    patch = """--- a/src/main/java/com/acme/OrderDTO.java
+++ b/src/main/java/com/acme/OrderDTO.java
@@ -1,7 +1,7 @@
 package com.acme;
 
 public class OrderDTO {
-    private String userId;
+    private String customerId;
     private int amount;
 
     public String getUserId() {
"""

    plan_result = engine.plan(PatchOperation(patch=patch, description="inline patch"))
    apply_result = engine.apply(PatchOperation(patch=patch, description="inline patch"))
    source = (java_project / "src/main/java/com/acme/OrderDTO.java").read_text(
        encoding="utf-8"
    )

    assert plan_result.is_valid
    assert plan_result.affected_files == ["src/main/java/com/acme/OrderDTO.java"]
    assert apply_result.success
    assert "private String customerId;" in source


def test_patch_operation_rejects_context_mismatch(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    patch = """--- a/src/main/java/com/acme/OrderDTO.java
+++ b/src/main/java/com/acme/OrderDTO.java
@@ -1,2 +1,2 @@
 package com.acme;
-class DoesNotExist {}
+class StillDoesNotExist {}
"""

    result = engine.apply(PatchOperation(patch=patch, description="bad patch"))

    assert not result.success
    assert result.errors[0]["type"] == "validation_failed"


def test_patch_operation_creates_new_file(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    patch = """--- /dev/null
+++ b/src/main/java/com/acme/NewDTO.java
@@ -0,0 +1,5 @@
+package com.acme;
+
+public class NewDTO {
+    private String id;
+}
"""

    result = engine.apply(PatchOperation(patch=patch, description="new file"))
    new_file = java_project / "src/main/java/com/acme/NewDTO.java"

    assert result.success
    assert new_file.exists()
    assert "class NewDTO" in new_file.read_text(encoding="utf-8")
    assert engine.graph is not None
    assert engine.graph.resolve_class("com.acme.NewDTO") is not None


def test_patch_operation_deletes_file(java_project: Path) -> None:
    extra_file = java_project / "src/main/java/com/acme/UnusedDTO.java"
    write(
        extra_file,
        """package com.acme;

public class UnusedDTO {
}
""",
    )
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    patch = """--- a/src/main/java/com/acme/UnusedDTO.java
+++ /dev/null
@@ -1,4 +0,0 @@
-package com.acme;
-
-public class UnusedDTO {
-}
"""

    result = engine.apply(PatchOperation(patch=patch, description="delete file"))

    assert result.success
    assert not extra_file.exists()
    assert engine.graph is not None
    assert engine.graph.resolve_class("com.acme.UnusedDTO") is None


def test_post_validation_catches_old_typed_field_access(java_project: Path) -> None:
    dto = java_project / "src/main/java/com/acme/OrderDTO.java"
    dto.write_text(
        """package com.acme;

public class OrderDTO {
    private String customerId;
    private int amount;
}
""",
        encoding="utf-8",
    )

    graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    violations = RuleValidator().validate_post(
        graph, RenameFieldOperation(target="com.acme.OrderDTO.userId", to="customerId")
    )

    assert any(item["type"] == "validation_failed" for item in violations)
