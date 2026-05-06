import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.engine.execution_engine import ExecutionEngine, _normalize_newlines, apply_lsp_edits
from core.graph.builder import GraphBuilder
from core.lsp.client import LspPosition, LspRange, LspTextEdit
from core.operation.models import RenameFieldOperation
from core.parser.java_parser import parse_java_project_static
from core.rules.validator import RuleValidator


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


def test_plan_accepts_unambiguous_simple_class_name(java_project: Path) -> None:
    engine = ExecutionEngine(java_project)
    graph = GraphBuilder(java_project).build(parse_java_project_static(java_project))
    engine.graph = graph

    result = engine.plan(RenameFieldOperation(target="OrderDTO.userId", to="customerId"))

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

    result = engine.apply(RenameFieldOperation(target="OrderDTO.userId", to="customerId"))

    assert not result.success
    assert result.errors[0]["type"] == "lsp_unavailable"


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
        graph, RenameFieldOperation(target="OrderDTO.userId", to="customerId")
    )

    assert any(item["type"] == "validation_failed" for item in violations)
