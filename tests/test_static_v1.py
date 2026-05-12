import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.commands.plan import _build_operation
from core.diff.patch_engine import apply_parsed_patch, parse_unified_patch
from core.engine.execution_engine import ExecutionEngine, _normalize_newlines, apply_lsp_edits
from core.graph.builder import GraphBuilder
from core.lsp.client import LspPosition, LspRange, LspTextEdit
from core.operation.models import PatchOperation
from core.parser.java_parser import parse_java_project_static


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
    graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))

    field = graph.resolve_field("OrderDTO", "userId")
    assert field is not None
    assert field.id == "com.acme.OrderDTO.userId"
    assert graph.get_affected_files_for_field("OrderDTO", "userId") == [
        "src/main/java/com/acme/OrderDTO.java",
        "src/main/java/com/acme/OrderService.java",
    ]


def test_cli_rejects_structured_edit_operations() -> None:
    with pytest.raises(ValueError, match="patch-only"):
        _build_operation("rename_field", "com.shop.UserDTO.userName", "customerName")
    with pytest.raises(ValueError, match="patch-only"):
        _build_operation("add_field", "com.shop.OrderDTO", "giftMessage", ["String"])
    with pytest.raises(ValueError, match="patch-only"):
        _build_operation("remove_field", "com.shop.OrderDTO", "giftMessage")


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
    assert "class NewName" in (operation.patch or "")


def test_cli_builds_patch_set_operation(tmp_path: Path) -> None:
    patch_one = tmp_path / "one.patch"
    patch_two = tmp_path / "two.patch"
    patch_one.write_text(
        """--- a/src/main/java/com/shop/OrderDTO.java
+++ b/src/main/java/com/shop/OrderDTO.java
@@ -1,2 +1,2 @@
 package com.shop;
-class First {}
+class Second {}
""",
        encoding="utf-8",
    )
    patch_two.write_text(
        """--- a/src/main/java/com/shop/OrderDTO.java
+++ b/src/main/java/com/shop/OrderDTO.java
@@ -1,2 +1,2 @@
 package com.shop;
-class Second {}
+class Third {}
""",
        encoding="utf-8",
    )

    operation = _build_operation("patch", str(patch_one), str(patch_two))

    assert isinstance(operation, PatchOperation)
    assert operation.patch_texts() == [
        patch_one.read_text(encoding="utf-8"),
        patch_two.read_text(encoding="utf-8"),
    ]


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


def test_parse_multiple_sections_with_git_rename_boundary() -> None:
    patch_files = parse_unified_patch(
        """--- a/src/main/java/com/acme/OrderDTO.java
+++ b/src/main/java/com/acme/OrderDTO.java
@@ -1,3 +1,3 @@
 class OrderDTO {
-    private String id;
+    private String externalId;
 }
diff --git a/src/main/java/com/acme/OldDTO.java b/src/main/java/com/acme/NewDTO.java
similarity index 100%
rename from src/main/java/com/acme/OldDTO.java
rename to src/main/java/com/acme/NewDTO.java
"""
    )

    assert len(patch_files) == 2
    assert patch_files[0].target_path == "src/main/java/com/acme/OrderDTO.java"
    assert patch_files[1].is_moved_file
    assert patch_files[1].move_only


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


def test_patch_set_applies_multiple_patches_to_same_virtual_file(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    first_patch = """--- a/src/main/java/com/acme/OrderDTO.java
+++ b/src/main/java/com/acme/OrderDTO.java
@@ -1,7 +1,7 @@
 package com.acme;
 
 public class OrderDTO {
-    private String userId;
+    private String customerId;
     private int amount;
 
     public String getUserId() {
"""
    second_patch = """--- a/src/main/java/com/acme/OrderDTO.java
+++ b/src/main/java/com/acme/OrderDTO.java
@@ -1,7 +1,7 @@
 package com.acme;
 
 public class OrderDTO {
-    private String customerId;
+    private String externalCustomerId;
     private int amount;
 
     public String getUserId() {
"""

    operation = PatchOperation(
        patch=first_patch,
        patches=[second_patch],
        description="ordered patch set",
    )
    plan_result = engine.plan(operation)
    apply_result = engine.apply(operation)
    source = (java_project / "src/main/java/com/acme/OrderDTO.java").read_text(
        encoding="utf-8"
    )

    assert plan_result.is_valid
    assert apply_result.success
    assert "private String externalCustomerId;" in source
    assert "private String customerId;" not in source


def test_patch_set_can_modify_new_file_before_commit(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    create_patch = """--- /dev/null
+++ b/src/main/java/com/acme/NewDTO.java
@@ -0,0 +1,5 @@
+package com.acme;
+
+public class NewDTO {
+    private String id;
+}
"""
    modify_patch = """--- a/src/main/java/com/acme/NewDTO.java
+++ b/src/main/java/com/acme/NewDTO.java
@@ -1,5 +1,5 @@
 package com.acme;
 
 public class NewDTO {
-    private String id;
+    private String externalId;
 }
"""

    result = engine.apply(
        PatchOperation(
            patch=create_patch,
            patches=[modify_patch],
            description="create then modify",
        )
    )
    new_file = java_project / "src/main/java/com/acme/NewDTO.java"

    assert result.success
    assert new_file.exists()
    assert "private String externalId;" in new_file.read_text(encoding="utf-8")
    assert engine.graph is not None
    assert engine.graph.resolve_field("com.acme.NewDTO", "externalId") is not None


def test_patch_plan_rejects_context_mismatch_before_pending_plan(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    patch = """--- a/src/main/java/com/acme/OrderDTO.java
+++ b/src/main/java/com/acme/OrderDTO.java
@@ -1,2 +1,2 @@
 package com.acme;
-class DoesNotExist {}
+class StillDoesNotExist {}
"""

    result = engine.plan(PatchOperation(patch=patch, description="bad patch"))

    assert not result.is_valid
    assert result.violations[0]["type"] == "validation_failed"


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


def test_patch_delete_is_excluded_from_virtual_post_validation(java_project: Path) -> None:
    duplicate_file = java_project / "src/main/java/com/acme/DuplicateDTO.java"
    write(
        duplicate_file,
        """package com.acme;

public class OrderDTO {
}
""",
    )
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    patch = """--- a/src/main/java/com/acme/DuplicateDTO.java
+++ /dev/null
@@ -1,4 +0,0 @@
-package com.acme;
-
-public class OrderDTO {
-}
"""

    result = engine.apply(PatchOperation(patch=patch, description="delete duplicate"))

    assert result.success
    assert not duplicate_file.exists()
    assert engine.graph is not None
    order_classes = [
        symbol
        for symbol in engine.graph.symbols
        if symbol.id == "com.acme.OrderDTO" and symbol.type.value == "class"
    ]
    assert len(order_classes) == 1


def test_patch_operation_moves_and_modifies_file(java_project: Path) -> None:
    old_file = java_project / "src/main/java/com/acme/MoveMe.java"
    write(
        old_file,
        """package com.acme;

public class MoveMe {
}
""",
    )
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    patch = """diff --git a/src/main/java/com/acme/MoveMe.java b/src/main/java/com/acme/MovedDTO.java
similarity index 80%
rename from src/main/java/com/acme/MoveMe.java
rename to src/main/java/com/acme/MovedDTO.java
--- a/src/main/java/com/acme/MoveMe.java
+++ b/src/main/java/com/acme/MovedDTO.java
@@ -1,4 +1,4 @@
 package com.acme;
 
-public class MoveMe {
+public class MovedDTO {
 }
"""

    result = engine.apply(PatchOperation(patch=patch, description="move and modify"))
    new_file = java_project / "src/main/java/com/acme/MovedDTO.java"

    assert result.success
    assert not old_file.exists()
    assert new_file.exists()
    assert engine.graph is not None
    assert engine.graph.resolve_class("com.acme.MovedDTO") is not None


def test_patch_operation_moves_file_without_hunks(java_project: Path) -> None:
    old_file = java_project / "src/main/java/com/acme/MoveOnlyDTO.java"
    write(
        old_file,
        """package com.acme;

public class MoveOnlyDTO {
}
""",
    )
    engine = ExecutionEngine(java_project)
    engine.graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    patch = """diff --git a/src/main/java/com/acme/MoveOnlyDTO.java b/src/main/java/com/acme/MovedOnlyDTO.java
similarity index 100%
rename from src/main/java/com/acme/MoveOnlyDTO.java
rename to src/main/java/com/acme/MovedOnlyDTO.java
"""

    result = engine.apply(PatchOperation(patch=patch, description="move only"))
    new_file = java_project / "src/main/java/com/acme/MovedOnlyDTO.java"

    assert result.success
    assert not old_file.exists()
    assert new_file.exists()
    assert "class MoveOnlyDTO" in new_file.read_text(encoding="utf-8")
