import sys
from pathlib import Path

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli.commands.errors import print_operation_errors
from cli.commands.plan import _build_operation
from core.diff.patch_engine import PatchParseError, apply_parsed_patch, parse_unified_patch
from core.engine.errors import EngineError
from core.engine.execution_engine import (
    ExecutionEngine,
    _format_lsp_diagnostic,
    _has_java_build_metadata,
    _snapshot_compile_command,
    _is_error_diagnostic,
    _normalize_newlines,
    validation_capability,
    apply_lsp_edits,
)
from core.graph.builder import GraphBuilder
from core.graph.semantic_graph import RefType
from core.lsp.client import LspClient, LspPosition, LspRange, LspTextEdit
from core.lsp.config import Language
from core.operation.models import PatchOperation
from core.parser.java_parser import parse_java_project_static
from utils.async_helpers import run_async


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


def test_graph_extracts_conservative_this_and_typed_method_references(tmp_path: Path) -> None:
    write(
        tmp_path / "src/main/java/com/acme/UserDTO.java",
        """package com.acme;

public class UserDTO {
    private String userName;

    public String getUserName() {
        return this.userName;
    }
}
""",
    )
    write(
        tmp_path / "src/main/java/com/acme/UserService.java",
        """package com.acme;

public class UserService {
    private UserDTO current;

    public String describe(UserDTO user) {
        String direct = user.getUserName();
        return this.current.getUserName() + direct;
    }
}
""",
    )

    graph = GraphBuilder(tmp_path).build(parse_java_project_static(tmp_path))
    user_name = graph.resolve_field("UserDTO", "userName")
    getter = graph.resolve_method("UserDTO", "getUserName")

    assert user_name is not None
    assert getter is not None
    assert any(
        ref.ref_type == RefType.FIELD_ACCESS
        and ref.from_symbol == getter.id
        and ref.to_symbol == user_name.id
        and ref.extra["receiver"] == "this"
        for ref in graph.references
    )
    method_refs = [
        ref
        for ref in graph.find_references_to(getter.id)
        if ref.ref_type == RefType.METHOD_CALL
    ]
    assert {ref.extra["receiver"] for ref in method_refs} == {"user", "this.current"}


def test_graph_method_ids_include_signatures_for_overloads(tmp_path: Path) -> None:
    write(
        tmp_path / "src/main/java/com/acme/UserDTO.java",
        """package com.acme;

public class UserDTO {
    public String label(String name) {
        return name;
    }

    public String label(int code) {
        return String.valueOf(code);
    }

    public String describe() {
        return label("x");
    }
}
""",
    )

    graph = GraphBuilder(tmp_path).build(parse_java_project_static(tmp_path))
    methods = graph.find_methods("UserDTO", "label")

    assert {method.id for method in methods} == {
        "com.acme.UserDTO.label(String)",
        "com.acme.UserDTO.label(int)",
    }
    assert {method.extra["signature"] for method in methods} == {"label(String)", "label(int)"}
    assert graph.resolve_method("UserDTO", "label") is None
    assert graph.resolve_method("UserDTO", "describe").id == "com.acme.UserDTO.describe()"


def test_cli_rejects_non_patch_operations() -> None:
    with pytest.raises(ValueError, match="patch-only"):
        _build_operation("custom_operation", "com.shop.UserDTO.userName", "customerName")


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


def test_patch_parser_rejects_binary_symlink_and_mode_only_patches() -> None:
    with pytest.raises(PatchParseError, match="Binary patches are not supported"):
        parse_unified_patch(
            """diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
"""
        )

    with pytest.raises(PatchParseError, match="Symlink patches are not supported"):
        parse_unified_patch(
            """diff --git a/link b/link
new file mode 120000
--- /dev/null
+++ b/link
@@ -0,0 +1 @@
+target
"""
        )

    with pytest.raises(PatchParseError, match="Mode-only or chmod patches are not supported"):
        parse_unified_patch(
            """diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
"""
        )


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


def test_lsp_diagnostic_helpers_format_errors(tmp_path: Path) -> None:
    java_file = tmp_path / "src/main/java/com/acme/UserDTO.java"
    write(java_file, "package com.acme;\nclass UserDTO {}\n")
    diagnostic = {
        "severity": 1,
        "message": "Cannot resolve symbol",
        "range": {"start": {"line": 1, "character": 6}},
    }

    assert _is_error_diagnostic(diagnostic)
    assert _is_error_diagnostic({"message": "server omitted severity"})
    assert not _is_error_diagnostic({"severity": 2, "message": "warning"})
    assert _format_lsp_diagnostic(java_file, diagnostic, tmp_path) == (
        "src/main/java/com/acme/UserDTO.java:2:7: Cannot resolve symbol"
    )


def test_java_build_metadata_detection(tmp_path: Path) -> None:
    assert not _has_java_build_metadata(tmp_path)
    assert validation_capability(tmp_path).java_build_metadata is False

    (tmp_path / "pom.xml").write_text("<project />", encoding="utf-8")

    assert _has_java_build_metadata(tmp_path)
    assert validation_capability(tmp_path).java_build_metadata is True


def test_static_parser_does_not_ignore_snapshot_root_inside_voyager_cache(tmp_path: Path) -> None:
    snapshot_root = tmp_path / ".voyager/cache/vfs-snapshots/patch-123"
    write(
        snapshot_root / "src/main/java/com/acme/SnapshotDTO.java",
        """package com.acme;

public class SnapshotDTO {
    private String id;
}
""",
    )

    classes = parse_java_project_static(snapshot_root)

    assert [cls.fqn for cls in classes] == ["com.acme.SnapshotDTO"]


def test_lsp_client_diagnostics_configuration_is_opt_in(tmp_path: Path) -> None:
    scan_client = LspClient(Language.JAVA, tmp_path)
    snapshot_client = LspClient(Language.JAVA, tmp_path, diagnostics_enabled=True)

    scan_java_settings = scan_client._workspace_settings()["java"]
    snapshot_java_settings = snapshot_client._workspace_settings()["java"]

    assert scan_java_settings["diagnostics"]["enabled"] is False
    assert snapshot_java_settings["diagnostics"]["enabled"] is True
    assert snapshot_java_settings["autobuild"]["enabled"] is True


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


def test_snapshot_diagnostics_reject_error_without_writing(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    operation = PatchOperation(patch="--- /dev/null\n+++ b/Noop.java\n@@ -0,0 +1,1 @@\n+class Noop {}\n")
    source = java_project / "src/main/java/com/acme/OrderDTO.java"
    snapshot = java_project / ".voyager/cache/vfs-snapshots/patch-test"
    snapshot_file = snapshot / "src/main/java/com/acme/OrderDTO.java"
    write(snapshot_file, source.read_text(encoding="utf-8"))

    class FakeSnapshotClient:
        async def wait_for_diagnostics(self, file_paths):
            assert snapshot_file in file_paths
            return {
                snapshot_file: [
                    {
                        "severity": 1,
                        "message": "Broken Java",
                        "range": {"start": {"line": 2, "character": 4}},
                    }
                ]
            }

    with pytest.raises(EngineError, match="LSP snapshot diagnostics failed"):
        run_async(
            engine._reject_snapshot_diagnostics_async(
                snapshot,
                FakeSnapshotClient(),
                operation,
            )
        )

    assert "private String userId;" in source.read_text(encoding="utf-8")


def test_snapshot_diagnostics_error_contains_structured_details(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    operation = PatchOperation(patch="--- /dev/null\n+++ b/Noop.java\n@@ -0,0 +1,1 @@\n+class Noop {}\n")
    snapshot = java_project / ".voyager/cache/vfs-snapshots/patch-test"
    snapshot_file = snapshot / "src/main/java/com/acme/OrderDTO.java"
    write(snapshot_file, "package com.acme;\nclass OrderDTO {}\n")

    class FakeSnapshotClient:
        async def wait_for_diagnostics(self, file_paths):
            return {
                snapshot_file: [
                    {
                        "severity": 1,
                        "message": "Cannot resolve symbol",
                        "range": {"start": {"line": 1, "character": 6}},
                        "source": "Java",
                    }
                ]
            }

    with pytest.raises(EngineError) as captured:
        run_async(
            engine._reject_snapshot_diagnostics_async(
                snapshot,
                FakeSnapshotClient(),
                operation,
            )
        )

    error = captured.value.to_dict()
    diagnostics = error["details"]["diagnostics"]
    assert diagnostics == [
        {
            "file": "src/main/java/com/acme/OrderDTO.java",
            "line": 2,
            "column": 7,
            "message": "Cannot resolve symbol",
            "severity": 1,
            "source": "Java",
            "code": None,
        }
    ]


def test_snapshot_compile_check_rejects_build_errors(
    monkeypatch: pytest.MonkeyPatch, java_project: Path
) -> None:
    engine = ExecutionEngine(java_project)
    operation = PatchOperation(patch="--- /dev/null\n+++ b/Noop.java\n@@ -0,0 +1,1 @@\n+class Noop {}\n")
    snapshot = java_project / ".voyager/cache/vfs-snapshots/compile-test"
    snapshot.mkdir(parents=True)
    (snapshot / "pom.xml").write_text("<project />", encoding="utf-8")

    monkeypatch.setattr(
        "core.engine.execution_engine._snapshot_compile_command",
        lambda path: ["fake-mvn", "test-compile"],
    )

    class FakeProcess:
        returncode = 1

        async def communicate(self):
            return b"cannot find symbol", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        assert args == ("fake-mvn", "test-compile")
        assert kwargs["cwd"] == str(snapshot)
        return FakeProcess()

    monkeypatch.setattr(
        "core.engine.execution_engine.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(EngineError, match="Snapshot compile check failed") as captured:
        run_async(engine._reject_snapshot_compile_errors_async(snapshot, operation))

    error = captured.value.to_dict()
    assert error["details"]["compile_check"]["command"] == ["fake-mvn", "test-compile"]
    assert error["details"]["compile_check"]["returncode"] == 1
    assert "cannot find symbol" in error["details"]["compile_check"]["output"]


def test_snapshot_compile_command_uses_javac_for_simple_maven_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write(
        tmp_path / "pom.xml",
        """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.acme</groupId>
  <artifactId>demo</artifactId>
  <version>1</version>
</project>
""",
    )
    write(
        tmp_path / "src/main/java/com/acme/UserDTO.java",
        "package com.acme;\npublic class UserDTO {}\n",
    )
    monkeypatch.setattr("core.engine.execution_engine.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "core.engine.execution_engine.shutil.which",
        lambda name: "javac" if name == "javac" else None,
    )

    command = _snapshot_compile_command(tmp_path)

    assert command is not None
    assert command[:3] == ["javac", "-d", str(tmp_path / ".voyager/cache/javac-classes")]
    assert str(tmp_path / "src/main/java/com/acme/UserDTO.java") in command


def test_cli_error_renderer_groups_structured_diagnostics() -> None:
    console = Console(record=True, color_system=None, width=120)
    print_operation_errors(
        console,
        "Plan rejected.",
        [
            {
                "type": "validation_failed",
                "message": "LSP snapshot diagnostics failed",
                "details": {
                    "diagnostics": [
                        {
                            "file": "src/main/java/com/acme/OrderDTO.java",
                            "line": 8,
                            "column": 16,
                            "message": "orderId cannot be resolved",
                        }
                    ]
                },
            }
        ],
    )

    output = console.export_text()
    assert "LSP snapshot diagnostics failed" in output
    assert "src/main/java/com/acme/OrderDTO.java" in output
    assert "8:16 orderId cannot be resolved" in output


def test_engine_ensure_graph_reuses_project_lsp_client(
    monkeypatch: pytest.MonkeyPatch, java_project: Path
) -> None:
    engine = ExecutionEngine(java_project)
    sentinel_client = object()
    seen = {}

    async def fake_parse(project_path, prefer_lsp=True, lsp_client=None):
        seen["project_path"] = project_path
        seen["lsp_client"] = lsp_client
        return parse_java_project_static(project_path)

    monkeypatch.setattr("core.engine.execution_engine.parse_java_project_async", fake_parse)
    engine.set_lsp_client(sentinel_client)

    graph = engine.ensure_graph(force_rebuild=True)

    assert graph.resolve_class("OrderDTO") is not None
    assert seen["project_path"] == java_project
    assert seen["lsp_client"] is sentinel_client


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


def test_patch_operation_rejects_non_utf8_target_file(java_project: Path) -> None:
    binary_file = java_project / "asset.bin"
    binary_file.write_bytes(b"\xff\xfe\x00")
    engine = ExecutionEngine(java_project)
    patch = """--- a/asset.bin
+++ b/asset.bin
@@ -1,1 +1,1 @@
-old
+new
"""

    result = engine.plan(PatchOperation(patch=patch, description="binary target"))

    assert not result.is_valid
    assert result.violations[0]["message"] == "Only UTF-8 text files can be patched: asset.bin"
