import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.engine.execution_engine import ExecutionEngine, _normalize_newlines, apply_lsp_edits
from core.graph.builder import GraphBuilder
from core.lsp.client import LspPosition, LspRange, LspTextEdit, LspWorkspaceEdit, path_to_uri
from core.operation.models import RenameClassOperation, RenameFieldOperation, RenameMethodOperation
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
