"""Voyager CLI entry point.

Usage:
    voyager start [<project_path>]
    voyager serve [<project_path>]
    voyager scan <project_path>
    voyager plan patch <patch_file|-> [<patch_file>...]
    voyager apply
    voyager status
    voyager progress
    voyager cancel
    voyager alita agent run <task>
    voyager alita tool plan-patch --patch <patch_file|->
    voyager alita tool apply-patch
    voyager alita tool status
    voyager stop
"""

import json
import logging
import sys

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Confirm
from rich.table import Table

from cli.commands.apply import apply_plan
from cli.commands.apply import _find_project_root as _find_project_root_for_status
from cli.commands.plan import plan_operation
from cli.commands.scan import scan_project
from core.engine.execution_engine import validation_capability
from core.server.client import VoyagerServerClient
from core.server.server import run_server

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
@click.version_option(version="0.1.0", prog_name="voyager")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """
    Voyager - Semantic code modification system.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


@cli.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False), required=False)
@click.pass_context
def serve(ctx: click.Context, project_path: str | None) -> None:
    """
    Run the Voyager server for a Java project.
    """
    from pathlib import Path

    root: Path = Path(project_path or ".").resolve()
    client: VoyagerServerClient = VoyagerServerClient(root, auto_start=False)
    try:
        status_result = client.status()
    except Exception:
        status_result = None

    if status_result and status_result.get("running"):
        console.print(f"[green]Voyager server already running for[/green] {root}")
        return

    console.print(f"[bold]Starting Voyager server[/bold] for {root}")
    console.print("[dim]Press Ctrl+C to stop the server.[/dim]")
    try:
        run_server(root)
    except KeyboardInterrupt:
        from storage.manager import StorageManager

        StorageManager(root).clear_server_info()
        console.print("\n[yellow]Voyager server stopped.[/yellow]")


@cli.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False), required=False)
@click.pass_context
def start(ctx: click.Context, project_path: str | None) -> None:
    """
    Start the Voyager server in the background.
    """
    from pathlib import Path

    root = Path(project_path or ".").resolve()
    try:
        result = VoyagerServerClient(root).start()
    except Exception as exc:
        console.print(f"[red]Failed to start Voyager server:[/red] {exc}")
        sys.exit(1)

    pid = result.get("pid", "unknown")
    console.print(f"[green]Voyager server running[/green] for {root} [dim](pid {pid})[/dim]")


@cli.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False))
@click.pass_context
def scan(ctx: click.Context, project_path: str) -> None:
    """
    Scan a Java project and build the semantic graph.
    """
    from pathlib import Path
    result = scan_project(Path(project_path))
    if result:
        console.print(f"[green]Scan complete:[/green] {result.symbols_count} symbols, {result.references_count} references")
    else:
        console.print("[red]Scan failed.[/red]")
        sys.exit(1)


@cli.command()
@click.argument(
    "op_type",
    type=click.Choice(
        [
            "patch",
        ]
    ),
)
@click.argument("target")
@click.argument("value", required=False)
@click.argument("extra", nargs=-1)
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
@click.pass_context
def plan(
    ctx: click.Context,
    op_type: str,
    target: str,
    value: str | None,
    extra: tuple[str, ...],
    json_output: bool,
) -> None:
    """
    Plan a Git-style unified diff patch without writing source files.

    PATCH_FILE may be '-' to read one patch from stdin. Multiple patch files are
    applied in order as one atomic patch set.
    """
    result = plan_operation(op_type, target, value, list(extra), json_output=json_output)
    if result is None or not result.is_valid:
        sys.exit(1)


@cli.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
@click.pass_context
def apply(ctx: click.Context, yes: bool, json_output: bool) -> None:
    """
    Apply the last planned operation.
    """
    result = apply_plan(skip_confirm=yes, json_output=json_output)
    if result is None or not result.get("success", False):
        sys.exit(1)


@cli.command()
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
@click.pass_context
def status(ctx: click.Context, json_output: bool) -> None:
    """
    Show current project status and graph info.
    """
    from storage.manager import StorageManager

    previous_log_disable = _suppress_logs_for_json() if json_output else None
    try:
        project_path = _find_project_root_for_status()
        manager = StorageManager(project_path)
        graph = manager.load_graph()

        try:
            server_status = VoyagerServerClient(project_path, auto_start=False).status()
            server_value = f"running (pid {server_status.get('pid')})"
        except Exception:
            server_value = "stopped"
            server_status = {}

        capabilities = server_status.get("capabilities") or validation_capability(project_path).to_dict()
        progress = server_status.get("progress") or {}

        if json_output:
            _print_json(
                _status_payload(project_path, graph, server_status, server_value, capabilities, progress)
            )
            return

        if graph is None:
            console.print("[yellow]No semantic graph found.[/yellow]")
            console.print("Run [bold]voyager scan <project_path>[/bold] to build one.")
            console.print(f"Voyager server: [cyan]{server_value}[/cyan]")
            console.print(f"JDT LS: [cyan]{_yes_no(capabilities.get('jdtls_available'))}[/cyan]")
            console.print(f"Java build metadata: [cyan]{_yes_no(capabilities.get('java_build_metadata'))}[/cyan]")
            console.print(
                f"Snapshot diagnostics: [cyan]{_yes_no(capabilities.get('snapshot_diagnostics'))}[/cyan]"
            )
            return

        table = Table(title="Voyager Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Server", server_value)
        table.add_row("JDT LS", _yes_no(capabilities.get("jdtls_available")))
        table.add_row("Java build metadata", _yes_no(capabilities.get("java_build_metadata")))
        table.add_row("Snapshot diagnostics", _yes_no(capabilities.get("snapshot_diagnostics")))
        if progress:
            table.add_row("Last operation", _format_progress(progress))
        counts = _graph_counts(graph)

        table.add_row("Classes", str(counts["classes"]))
        table.add_row("Fields", str(counts["fields"]))
        table.add_row("Methods", str(counts["methods"]))
        table.add_row("References", str(counts["references"]))

        console.print(table)
    finally:
        if previous_log_disable is not None:
            logging.disable(previous_log_disable)


@cli.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """
    Stop the Voyager server for the current project.
    """
    project_path = _find_project_root_for_status()
    try:
        VoyagerServerClient(project_path, auto_start=False).shutdown()
    except Exception as exc:
        console.print(f"[yellow]No running Voyager server found:[/yellow] {exc}")
        return
    console.print("[green]Voyager server stopped.[/green]")


@cli.command()
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
@click.pass_context
def progress(ctx: click.Context, json_output: bool) -> None:
    """
    Show the last known Server operation progress.
    """
    project_path = _find_project_root_for_status()
    try:
        result = VoyagerServerClient(project_path, auto_start=False).progress()
    except Exception as exc:
        if json_output:
            _print_json({"available": False, "error": {"type": exc.__class__.__name__, "message": str(exc)}})
            return
        console.print(f"[yellow]No running Voyager server found:[/yellow] {exc}")
        return
    if json_output:
        _print_json({"available": True, "progress": result})
        return
    console.print(f"[cyan]{_format_progress(result)}[/cyan]")
    message = result.get("message")
    if message:
        console.print(str(message))


@cli.command()
@click.pass_context
def cancel(ctx: click.Context) -> None:
    """
    Request cancellation of the current Server operation.
    """
    project_path = _find_project_root_for_status()
    try:
        result = VoyagerServerClient(project_path, auto_start=False).cancel()
    except Exception as exc:
        console.print(f"[yellow]No running Voyager server found:[/yellow] {exc}")
        return
    style = "green" if result.get("accepted") else "yellow"
    console.print(f"[{style}]{result.get('message', 'Cancel request completed.')}[/{style}]")


@cli.group()
def alita() -> None:
    """
    Alita Agent product layer commands.
    """


@alita.command("run")
@click.argument("task")
@click.option(
    "--patch",
    "patch_source",
    required=True,
    help="Git-style unified diff file, or '-' to read one patch from stdin.",
)
@click.option(
    "--active-file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False),
    help="Optional active file hint from the IDE/client.",
)
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
def alita_run(task: str, patch_source: str, active_file: str | None, json_output: bool) -> None:
    """
    Create an Alita run record and plan a supplied patch.

    This MVP does not call a model and does not apply files. It builds a
    context pack, records the patch attempt, calls Voyager plan, and stops at
    the plan result.
    """
    from pathlib import Path

    from alita.run import run_plan_mvp

    project_path = _find_project_root_for_status()
    try:
        result = run_plan_mvp(
            project_path,
            task,
            patch_source=patch_source,
            active_file=Path(active_file) if active_file else None,
        )
    except Exception as exc:
        if json_output:
            _print_json(
                {
                    "success": False,
                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                }
            )
        else:
            console.print(f"[red]Alita run failed:[/red] {exc}")
        sys.exit(1)

    if json_output:
        _print_json(result.to_dict())
    else:
        _print_alita_run_result(result)

    if not result.success:
        sys.exit(1)


@alita.group("agent")
def alita_agent() -> None:
    """
    Runtime-backed Alita agent commands.
    """


@alita_agent.command("run")
@click.argument("task")
@click.option(
    "--runtime",
    "runtime_name",
    type=click.Choice(["manual", "adk"]),
    default="manual",
    show_default=True,
    help="Runtime backend.",
)
@click.option(
    "--provider",
    "provider_name",
    type=click.Choice(["openai", "gemini", "anthropic", "qwen", "doubao", "kimi", "glm"]),
    default="gemini",
    show_default=True,
    help="Model provider profile.",
)
@click.option("--model", help="Provider model name. Required for ADK runtime.")
@click.option("--provider-base-url", help="Override provider base URL.")
@click.option("--api-key-env", help="Override provider API key environment variable.")
@click.option(
    "--patch",
    "patch_source",
    help="Manual-runtime patch source, or '-' to read one patch from stdin.",
)
@click.option(
    "--active-file",
    type=click.Path(exists=False, file_okay=True, dir_okay=False),
    help="Optional active file hint from the IDE/client.",
)
@click.option(
    "--write-policy",
    type=click.Choice(["manual_confirm", "allowlist", "denylist", "auto_execute"]),
    default="manual_confirm",
    show_default=True,
    help="Write policy evaluated after a valid plan.",
)
@click.option("--allow", "allow_rules", multiple=True, help="Allowlist glob rule.")
@click.option("--deny", "deny_rules", multiple=True, help="Denylist glob rule.")
@click.option(
    "--require-confirm",
    "require_confirm_rules",
    multiple=True,
    help="Glob rule that always asks for confirmation.",
)
@click.option(
    "--fallback-action",
    type=click.Choice(["allow", "deny", "ask_user"]),
    default="ask_user",
    show_default=True,
    help="Fallback action for denylist policy when no deny rule matches.",
)
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
def alita_agent_run(
    task: str,
    runtime_name: str,
    provider_name: str,
    model: str | None,
    provider_base_url: str | None,
    api_key_env: str | None,
    patch_source: str | None,
    active_file: str | None,
    write_policy: str,
    allow_rules: tuple[str, ...],
    deny_rules: tuple[str, ...],
    require_confirm_rules: tuple[str, ...],
    fallback_action: str,
    json_output: bool,
) -> None:
    """
    Run one runtime-backed Alita agent turn and plan the produced patch.
    """
    from pathlib import Path

    from alita.agent import run_agent_once
    from alita.tool_commands import build_write_policy

    policy = build_write_policy(
        mode=_write_policy_mode(write_policy),
        allow=allow_rules,
        deny=deny_rules,
        require_confirm=require_confirm_rules,
        fallback_action=_policy_action(fallback_action),
    )
    try:
        result = run_agent_once(
            _find_project_root_for_status(),
            task,
            runtime_name=runtime_name,
            provider_name=provider_name,
            model=model,
            provider_base_url=provider_base_url,
            provider_api_key_env=api_key_env,
            patch_source=patch_source,
            active_file=Path(active_file) if active_file else None,
            write_policy=policy,
        )
    except Exception as exc:
        if json_output:
            _print_json(
                {
                    "success": False,
                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                }
            )
        else:
            console.print(f"[red]Alita agent run failed:[/red] {exc}")
        sys.exit(1)

    if json_output:
        _print_json(result.to_dict())
    else:
        _print_alita_agent_result(result)

    if not result.success:
        sys.exit(1)


@alita.group("tool")
def alita_tool() -> None:
    """
    CLI-first Alita tools for agents and automation.
    """


@alita_tool.command("plan-patch")
@click.option(
    "--patch",
    "patch_sources",
    multiple=True,
    required=True,
    help="Git-style unified diff file, or '-' to read one patch from stdin. Repeat for ordered patch sets.",
)
@click.option(
    "--write-policy",
    type=click.Choice(["manual_confirm", "allowlist", "denylist", "auto_execute"]),
    default="manual_confirm",
    show_default=True,
    help="Write policy used to decide whether a valid plan can become pending.",
)
@click.option("--allow", "allow_rules", multiple=True, help="Allowlist glob rule.")
@click.option("--deny", "deny_rules", multiple=True, help="Denylist glob rule.")
@click.option(
    "--require-confirm",
    "require_confirm_rules",
    multiple=True,
    help="Glob rule that always asks for confirmation.",
)
@click.option(
    "--fallback-action",
    type=click.Choice(["allow", "deny", "ask_user"]),
    default="ask_user",
    show_default=True,
    help="Fallback action for denylist policy when no deny rule matches.",
)
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
def alita_tool_plan_patch(
    patch_sources: tuple[str, ...],
    write_policy: str,
    allow_rules: tuple[str, ...],
    deny_rules: tuple[str, ...],
    require_confirm_rules: tuple[str, ...],
    fallback_action: str,
    json_output: bool,
) -> None:
    """
    Plan a patch through the policy-aware Alita tool registry.
    """
    from alita.tool_commands import build_write_policy, plan_patch_tool

    policy = build_write_policy(
        mode=_write_policy_mode(write_policy),
        allow=allow_rules,
        deny=deny_rules,
        require_confirm=require_confirm_rules,
        fallback_action=_policy_action(fallback_action),
    )
    result = plan_patch_tool(
        _find_project_root_for_status(),
        list(patch_sources),
        write_policy=policy,
    )

    if json_output:
        _print_json(result.to_dict())
    else:
        _print_alita_tool_plan_result(result)

    if not result.success:
        sys.exit(1)


@alita_tool.command("apply-patch")
@click.option(
    "--plan",
    "plan_ref",
    type=click.Choice(["current"]),
    default="current",
    show_default=True,
    help="Pending plan to apply.",
)
@click.option("--yes", "-y", is_flag=True, help="Approve ask-user policy decisions")
@click.option(
    "--write-policy",
    type=click.Choice(["manual_confirm", "allowlist", "denylist", "auto_execute"]),
    default="manual_confirm",
    show_default=True,
    help="Write policy used before applying source changes.",
)
@click.option("--allow", "allow_rules", multiple=True, help="Allowlist glob rule.")
@click.option("--deny", "deny_rules", multiple=True, help="Denylist glob rule.")
@click.option(
    "--require-confirm",
    "require_confirm_rules",
    multiple=True,
    help="Glob rule that always asks for confirmation.",
)
@click.option(
    "--fallback-action",
    type=click.Choice(["allow", "deny", "ask_user"]),
    default="ask_user",
    show_default=True,
    help="Fallback action for denylist policy when no deny rule matches.",
)
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
def alita_tool_apply_patch(
    plan_ref: str,
    yes: bool,
    write_policy: str,
    allow_rules: tuple[str, ...],
    deny_rules: tuple[str, ...],
    require_confirm_rules: tuple[str, ...],
    fallback_action: str,
    json_output: bool,
) -> None:
    """
    Apply the current pending patch through Alita HITL policy.
    """
    from alita.tool_commands import apply_patch_tool, build_write_policy

    policy = build_write_policy(
        mode=_write_policy_mode(write_policy),
        allow=allow_rules,
        deny=deny_rules,
        require_confirm=require_confirm_rules,
        fallback_action=_policy_action(fallback_action),
    )

    approval_callback = None if json_output else _confirm_alita_apply
    result = apply_patch_tool(
        _find_project_root_for_status(),
        write_policy=policy,
        user_approved=yes,
        approval_callback=approval_callback,
    )

    if json_output:
        _print_json(result.to_dict())
    else:
        _print_alita_tool_apply_result(result)

    if not result.success:
        sys.exit(1)


@alita_tool.command("status")
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON")
def alita_tool_status(json_output: bool) -> None:
    """
    Show local Alita tool status.
    """
    from alita.tool_commands import status_tool

    result = status_tool(_find_project_root_for_status())
    if json_output:
        _print_json(result.to_dict())
    else:
        _print_alita_tool_status(result.to_dict()["status"] or {})


def _write_policy_mode(value: str):
    from alita.policy import WritePolicyMode

    return WritePolicyMode(value)


def _policy_action(value: str):
    from alita.policy import PolicyAction

    return PolicyAction(value)


def _confirm_alita_apply(policy_decision: object, write_intent: object) -> bool:
    affected_files = getattr(write_intent, "affected_files", [])
    reason = getattr(policy_decision, "reason", "Policy requires confirmation.")
    console.print(f"[yellow]{reason}[/yellow]")
    if affected_files:
        console.print("[bold]Affected files:[/bold]")
        for file_path in affected_files:
            console.print(f"  - {file_path}")
    return Confirm.ask("Approve this Alita write?")


def _yes_no(value: object) -> str:
    return "yes" if value is True else "no"


def _format_progress(progress: dict) -> str:
    status = progress.get("status") or "idle"
    stage = progress.get("stage") or "idle"
    cancel = " cancel requested" if progress.get("cancel_requested") else ""
    return f"{stage} / {status}{cancel}"


def _graph_counts(graph: object | None) -> dict[str, int]:
    if graph is None:
        return {"classes": 0, "fields": 0, "methods": 0, "references": 0}
    symbols = getattr(graph, "symbols", [])
    return {
        "classes": len([symbol for symbol in symbols if symbol.type.value == "class"]),
        "fields": len([symbol for symbol in symbols if symbol.type.value == "field"]),
        "methods": len([symbol for symbol in symbols if symbol.type.value == "method"]),
        "references": len(getattr(graph, "references", [])),
    }


def _status_payload(
    project_path: object,
    graph: object | None,
    server_status: dict,
    server_value: str,
    capabilities: dict,
    progress: dict,
) -> dict:
    return {
        "project_path": str(project_path),
        "server": {
            "running": bool(server_status.get("running")),
            "status": server_value,
            "pid": server_status.get("pid"),
        },
        "capabilities": capabilities,
        "progress": progress,
        "graph": {
            "exists": graph is not None,
            **_graph_counts(graph),
        },
    }


def _print_json(data: dict) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")


def _suppress_logs_for_json() -> int:
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    return previous


def _print_alita_run_result(result: object) -> None:
    console.print(f"[green]Alita run created:[/green] {result.run_id}")
    console.print(f"  Run directory: {result.run_dir}")
    plan_result = result.plan_result
    if plan_result is None:
        console.print("[red]No plan result was produced.[/red]")
        return
    if plan_result.is_valid:
        console.print(
            f"[green]Plan valid.[/green] {len(plan_result.affected_files)} file(s) affected:"
        )
        for file_path in plan_result.affected_files:
            console.print(f"  - {file_path}")
        console.print("[yellow]Apply was not run. Use voyager apply -y when policy allows.[/yellow]")
    else:
        console.print("[red]Plan rejected.[/red]")
        for violation in plan_result.violations:
            console.print(f"  [red]{violation.get('type', 'error')}:[/red] {violation.get('message', violation)}")


def _print_alita_agent_result(result: object) -> None:
    console.print(f"[green]Alita agent run created:[/green] {result.run_id}")
    console.print(f"  Run directory: {result.run_dir}")
    runtime_result = result.runtime_result
    if not runtime_result.success:
        console.print(f"[red]Runtime failed:[/red] {runtime_result.runtime_name}")
        for error in runtime_result.errors:
            console.print(f"  [red]{error.get('type', 'error')}:[/red] {error.get('message', error)}")
        return

    plan_result = result.plan_result
    if plan_result is None:
        console.print("[red]Runtime produced no plan result.[/red]")
        return

    if plan_result.is_valid:
        console.print(
            f"[green]Plan valid.[/green] {len(plan_result.affected_files)} file(s) affected:"
        )
        for file_path in plan_result.affected_files:
            console.print(f"  - {file_path}")
        decision = result.policy_decision
        if decision is not None:
            console.print(f"Policy decision: [cyan]{decision.action.value}[/cyan] ({decision.reason})")
        console.print("[yellow]Apply was not run. Use alita tool apply-patch after approval.[/yellow]")
    else:
        console.print("[red]Plan rejected.[/red]")
        for violation in plan_result.violations:
            console.print(f"  [red]{violation.get('type', 'error')}:[/red] {violation.get('message', violation)}")


def _print_alita_tool_plan_result(result: object) -> None:
    plan_result = result.plan_result
    if plan_result is None:
        console.print("[red]Alita plan-patch failed.[/red]")
        for error in result.errors or []:
            console.print(f"  [red]{error.get('type', 'error')}:[/red] {error.get('message', error)}")
        return

    if plan_result.is_valid:
        console.print(
            f"[green]Alita plan valid.[/green] {len(plan_result.affected_files)} file(s) affected:"
        )
        for file_path in plan_result.affected_files:
            console.print(f"  - {file_path}")
        decision = result.policy_decision
        if decision is not None:
            console.print(f"Policy decision: [cyan]{decision.action.value}[/cyan] ({decision.reason})")
        if result.pending_plan_saved:
            console.print("[green]Pending plan saved for apply-patch.[/green]")
        else:
            console.print("[yellow]Pending plan was not saved.[/yellow]")
    else:
        console.print("[red]Alita plan rejected.[/red]")
        for violation in plan_result.violations:
            console.print(f"  [red]{violation.get('type', 'error')}:[/red] {violation.get('message', violation)}")


def _print_alita_tool_apply_result(result: object) -> None:
    apply_call = result.apply_tool_call
    if result.success:
        console.print("[green]Alita apply-patch completed.[/green]")
        payload = apply_call.payload if apply_call is not None else {}
        for file_path in payload.get("modified_files", []):
            console.print(f"  Modified: {file_path}")
        return

    decision = result.policy_decision
    if decision is not None:
        console.print(f"[yellow]Policy decision:[/yellow] {decision.action.value} ({decision.reason})")
    errors = result.errors or []
    if errors:
        console.print("[red]Alita apply-patch did not execute.[/red]")
        for error in errors:
            console.print(f"  [red]{error.get('type', 'error')}:[/red] {error.get('message', error)}")
    else:
        console.print("[red]Alita apply-patch failed.[/red]")


def _print_alita_tool_status(status_payload: dict) -> None:
    console.print("[bold]Alita Tool Status[/bold]")
    console.print(f"Project: {status_payload.get('project_path')}")
    pending = status_payload.get("pending_plan") or {}
    if pending.get("exists"):
        console.print("[green]Pending plan:[/green] yes")
        console.print(f"  Operation: {pending.get('operation')}")
        console.print(f"  Patch count: {pending.get('patch_count')}")
        description = pending.get("description")
        if description:
            console.print(f"  Description: {description}")
    else:
        console.print("[yellow]Pending plan:[/yellow] no")

    latest = status_payload.get("latest_run")
    if latest:
        console.print("[green]Latest Alita run:[/green]")
        console.print(f"  Run ID: {latest.get('run_id')}")
        console.print(f"  Status: {latest.get('status')}")
        console.print(f"  Task: {latest.get('task')}")
    else:
        console.print("[yellow]Latest Alita run:[/yellow] none")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli()
        sys.exit(0)

    # ── IDE / script mode: edit the values below and run directly ──────────
    from voyager_cmd.runner import VoyagerRunner

    cli()
