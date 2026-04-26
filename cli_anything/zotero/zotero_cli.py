from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from cli_anything.zotero import __version__
from cli_anything.zotero.core import analysis, catalog, discovery, docx as docx_tools, docx_static, docx_zoterify, experimental, imports, jsbridge, metrics, notes, rendering, semantic, session as session_mod
from cli_anything.zotero.utils import zotero_paths
from cli_anything.zotero.utils.repl_skin import ReplSkin

try:
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
except Exception:  # pragma: no cover - platform-specific import guard
    NoConsoleScreenBufferError = RuntimeError


CONTEXT_SETTINGS = {"ignore_unknown_options": False}


def _format_params(cmd: click.Command) -> str:
    """Format a command's parameters as a compact string for help display."""
    params = []
    for p in cmd.params:
        if isinstance(p, click.Argument):
            params.append(p.human_readable_name)
        elif not p.hidden and p.name != "help":
            opt_str = "/".join(p.opts)
            if p.is_flag:
                params.append(f"[{opt_str}]")
            else:
                params.append(f"[{opt_str} <{p.type.name}>]")
    return " ".join(params)


def _format_help_all(group: click.Group, ctx: click.Context, prefix: str = "", depth: int = 0) -> str:
    """Recursively format help for all commands in a Click group."""
    lines: list[str] = []
    indent = "  " * (depth + 1)
    for name in sorted(group.list_commands(ctx)):
        cmd = group.get_command(ctx, name)
        if cmd is None:
            continue
        full_name = f"{prefix} {name}".strip()
        help_text = cmd.get_short_help_str(limit=80)
        if isinstance(cmd, click.Group):
            lines.append(f"\n{indent}{full_name}")
            lines.append(f"{indent}  {help_text}")
            sub_ctx = click.Context(cmd, info_name=name, parent=ctx)
            lines.append(_format_help_all(cmd, sub_ctx, prefix=full_name, depth=depth + 1))
        else:
            param_str = _format_params(cmd)
            lines.append(f"{indent}{full_name} {param_str}")
            lines.append(f"{indent}  {help_text}")
    return "\n".join(lines)


def _propagate_json_flag(ctx: click.Context, args: list[str]) -> list[str]:
    """Extract ``--json`` from *args* and bubble it up to the root context.

    At the root level, leave ``--json`` in args so Click can process it
    normally through its own option.  At sub-levels (groups/commands),
    remove it from args and propagate to the root context.
    """
    if "--json" not in args:
        return args
    root = ctx.find_root()
    if ctx is root:
        # Root level: let Click handle --json via its own @click.option
        return args
    args = list(args)
    args.remove("--json")
    root.ensure_object(dict)
    root.obj["json_output"] = True
    cli_config = root.obj.get("cli_config")
    if isinstance(cli_config, RootCliConfig):
        root.obj["cli_config"] = RootCliConfig(
            backend=cli_config.backend,
            data_dir=cli_config.data_dir,
            profile_dir=cli_config.profile_dir,
            executable=cli_config.executable,
            json_output=True,
        )
    return args


class _JsonAwareGroup(click.Group):
    """A Click Group that accepts ``--json`` at any level and propagates it to the root context.

    This allows users to write ``zotero-cli collection list --json``
    instead of requiring ``zotero-cli --json collection list``.

    All sub-groups and commands created via this group inherit the same behavior.
    """

    group_class = None  # set after class definition
    command_class = None  # set after class definition

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        args = _propagate_json_flag(ctx, args)
        return super().parse_args(ctx, args)

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Override help to show all commands recursively for the root group."""
        if ctx.parent is not None:
            return super().format_help(ctx, formatter)
        self.format_usage(ctx, formatter)
        formatter.write("\n")
        if self.help:
            formatter.write_paragraph()
            with formatter.indentation():
                formatter.write(self.help)
            formatter.write("\n")
        opts = []
        for p in self.params:
            rv = p.get_help_record(ctx)
            if rv is not None:
                opts.append(rv)
        if opts:
            with formatter.section("Global Options"):
                formatter.write_dl(opts)
        formatter.write("\n")
        formatter.write("All commands:\n")
        formatter.write(_format_help_all(self, ctx))
        formatter.write("\n")


class _JsonAwareCommand(click.Command):
    """A Click Command that accepts ``--json`` and propagates it to the root context."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        args = _propagate_json_flag(ctx, args)
        return super().parse_args(ctx, args)


# Wire up so that sub-groups/commands auto-inherit the json-aware behavior.
_JsonAwareGroup.group_class = _JsonAwareGroup
_JsonAwareGroup.command_class = _JsonAwareCommand


@dataclass(frozen=True)
class RootCliConfig:
    backend: str = "auto"
    data_dir: str | None = None
    profile_dir: str | None = None
    executable: str | None = None
    json_output: bool = False


def _stdout_encoding() -> str:
    return getattr(sys.stdout, "encoding", None) or "utf-8"


def _can_encode_for_stdout(text: str) -> bool:
    try:
        text.encode(_stdout_encoding())
    except UnicodeEncodeError:
        return False
    return True


def _safe_text_for_stdout(text: str) -> str:
    if _can_encode_for_stdout(text):
        return text
    return text.encode(_stdout_encoding(), errors="backslashreplace").decode(_stdout_encoding())


def _json_text(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if _can_encode_for_stdout(text):
        return text
    return json.dumps(data, ensure_ascii=True, indent=2)


def root_json_output(ctx: click.Context | None) -> bool:
    if ctx is None:
        return False
    root = ctx.find_root()
    if root is None or root.obj is None:
        return False
    cli_config = root.obj.get("cli_config")
    if isinstance(cli_config, RootCliConfig):
        return cli_config.json_output
    return bool(root.obj.get("json_output"))


def _build_runtime_from_config(config: RootCliConfig) -> discovery.RuntimeContext:
    return discovery.build_runtime_context(
        backend=config.backend,
        data_dir=config.data_dir,
        profile_dir=config.profile_dir,
        executable=config.executable,
    )


def _current_cli_config(ctx: click.Context | None) -> RootCliConfig:
    if ctx is None:
        return RootCliConfig()
    root = ctx.find_root()
    assert root is not None
    root.ensure_object(dict)
    cli_config = root.obj.get("cli_config")
    if isinstance(cli_config, RootCliConfig):
        return cli_config
    legacy = root.obj.get("config", {})
    cli_config = RootCliConfig(
        backend=legacy.get("backend", "auto"),
        data_dir=legacy.get("data_dir"),
        profile_dir=legacy.get("profile_dir"),
        executable=legacy.get("executable"),
        json_output=bool(root.obj.get("json_output")),
    )
    root.obj["cli_config"] = cli_config
    return cli_config


def _repl_root_args(config: RootCliConfig) -> list[str]:
    args = ["--backend", config.backend]
    if config.json_output:
        args.append("--json")
    if config.data_dir:
        args.extend(["--data-dir", config.data_dir])
    if config.profile_dir:
        args.extend(["--profile-dir", config.profile_dir])
    if config.executable:
        args.extend(["--executable", config.executable])
    return args


def current_runtime(ctx: click.Context) -> discovery.RuntimeContext:
    root = ctx.find_root()
    assert root is not None
    root.ensure_object(dict)
    cached = root.obj.get("runtime")
    config = _current_cli_config(ctx)
    if cached is None:
        cached = _build_runtime_from_config(config)
        root.obj["runtime"] = cached
    return cached


def current_bridge(ctx: click.Context) -> jsbridge.JSBridgeClient:
    """Get a JS Bridge client bound to the runtime's discovered port."""
    root = ctx.find_root()
    root.ensure_object(dict)
    cached = root.obj.get("bridge")
    if cached is None:
        runtime = current_runtime(ctx)
        cached = jsbridge.JSBridgeClient(port=runtime.environment.port)
        root.obj["bridge"] = cached
    return cached


def current_session() -> dict[str, Any]:
    return session_mod.load_session_state()


def emit(ctx: click.Context | None, data: Any, *, message: str = "") -> None:
    if root_json_output(ctx):
        click.echo(_json_text(data))
        return
    if isinstance(data, str):
        click.echo(_safe_text_for_stdout(data))
        return
    if message:
        click.echo(_safe_text_for_stdout(message))
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                click.echo(_json_text(item))
            else:
                click.echo(_safe_text_for_stdout(str(item)))
        if not data:
            click.echo("[]")
        return
    if isinstance(data, dict):
        click.echo(_json_text(data))
        return
    click.echo(_safe_text_for_stdout(str(data)))


def emit_js(ctx: click.Context | None, result: dict) -> int:
    """Emit a JS bridge result. Outputs result['data'] if available, else the full dict.
    Returns exit code (0=ok, 1=error)."""
    if not result.get("ok"):
        emit(ctx, result)
        return 1
    data = result.get("data")
    if data is not None:
        emit(ctx, data)
    else:
        emit(ctx, result)
    return 0


def _print_collection_tree(nodes: list[dict[str, Any]], level: int = 0) -> None:
    prefix = "  " * level
    for node in nodes:
        click.echo(f"{prefix}- {node['collectionName']} [{node['collectionID']}]")
        _print_collection_tree(node.get("children", []), level + 1)


def _require_experimental_flag(enabled: bool, command_name: str) -> None:
    if not enabled:
        raise click.ClickException(
            f"`{command_name}` is experimental and writes directly to zotero.sqlite. "
            "Pass --experimental to continue."
        )


def _normalize_session_library(runtime: discovery.RuntimeContext, library_ref: str) -> int:
    try:
        library_id = catalog.resolve_library_id(runtime, library_ref)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    if library_id is None:
        raise click.ClickException("Library reference required")
    return library_id


def _import_exit_code(payload: dict[str, Any]) -> int:
    return 1 if payload.get("status") == "partial_success" else 0


@click.group(
    context_settings=CONTEXT_SETTINGS,
    invoke_without_command=True,
    cls=_JsonAwareGroup,
)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--backend", type=click.Choice(["auto", "sqlite", "api"]), default="auto", show_default=True)
@click.option("--data-dir", default=None, help="Explicit Zotero data directory.")
@click.option("--profile-dir", default=None, help="Explicit Zotero profile directory.")
@click.option("--executable", default=None, help="Explicit Zotero executable path.")
@click.version_option(__version__, prog_name="cli-anything-zotero")
@click.pass_context
def cli(ctx: click.Context, json_output: bool, backend: str, data_dir: str | None, profile_dir: str | None, executable: str | None) -> int:
    """Agent-native Zotero CLI using SQLite, connector, and Local API backends."""
    ctx.ensure_object(dict)
    cli_config = RootCliConfig(
        backend=backend,
        data_dir=data_dir,
        profile_dir=profile_dir,
        executable=executable,
        json_output=json_output,
    )
    ctx.obj["json_output"] = json_output
    ctx.obj["cli_config"] = cli_config
    ctx.obj["config"] = {
        "backend": backend,
        "data_dir": data_dir,
        "profile_dir": profile_dir,
        "executable": executable,
    }
    if ctx.invoked_subcommand is None:
        return run_repl(cli_config)
    return 0


@cli.group()
def library() -> None:
    """Library inspection commands."""


@library.command("list")
@click.pass_context
def library_list(ctx: click.Context) -> int:
    runtime = current_runtime(ctx)
    emit(ctx, catalog.list_libraries(runtime))
    return 0


@cli.group()
def app() -> None:
    """Application and runtime inspection commands."""


@app.command("status")
@click.pass_context
def app_status(ctx: click.Context) -> int:
    runtime = current_runtime(ctx)
    emit(ctx, runtime.to_status_payload())
    return 0


@app.command("version")
@click.pass_context
def app_version(ctx: click.Context) -> int:
    runtime = current_runtime(ctx)
    payload = {"package_version": __version__, "zotero_version": runtime.environment.version}
    emit(ctx, payload if root_json_output(ctx) else runtime.environment.version)
    return 0


@app.command("launch")
@click.option("--wait-timeout", default=30, show_default=True, type=int)
@click.pass_context
def app_launch(ctx: click.Context, wait_timeout: int) -> int:
    runtime = current_runtime(ctx)
    payload = discovery.launch_zotero(runtime, wait_timeout=wait_timeout)
    ctx.find_root().obj["runtime"] = None
    emit(ctx, payload)
    return 0


@app.command("enable-local-api")
@click.option("--launch", "launch_after_enable", is_flag=True, help="Launch Zotero and verify connector + Local API after enabling.")
@click.option("--wait-timeout", default=30, show_default=True, type=int)
@click.pass_context
def app_enable_local_api(ctx: click.Context, launch_after_enable: bool, wait_timeout: int) -> int:
    payload = imports.enable_local_api(current_runtime(ctx), launch=launch_after_enable, wait_timeout=wait_timeout)
    ctx.find_root().obj["runtime"] = None
    emit(ctx, payload)
    return 0


@app.command("ping")
@click.pass_context
def app_ping(ctx: click.Context) -> int:
    runtime = current_runtime(ctx)
    if not runtime.connector_available:
        raise click.ClickException(runtime.connector_message)
    emit(ctx, {"connector_available": True, "message": runtime.connector_message})
    return 0


@app.command("install-plugin")
@click.pass_context
def app_install_plugin(ctx: click.Context) -> int:
    """Install the CLI Bridge plugin into Zotero.

    Builds the .xpi and attempts programmatic installation via the JS bridge.
    If that is not available, saves the .xpi and prints manual install instructions.
    """
    runtime = current_runtime(ctx)
    profile_dir = runtime.environment.profile_dir
    if profile_dir is None:
        raise click.ClickException("Cannot resolve Zotero profile directory.")
    xpi_path = zotero_paths.install_plugin_xpi(profile_dir)

    # Try programmatic install via JS bridge (works when bridge is already active)
    if current_bridge(ctx).bridge_endpoint_active():
        js = (
            "var {AddonManager} = ChromeUtils.importESModule("
            "'resource://gre/modules/AddonManager.sys.mjs'); "
            f"var file = Zotero.File.pathToFile('{str(xpi_path).replace(chr(92), '/')}'); "
            "var install = await AddonManager.getInstallForFile(file); "
            "await install.install(); "
            "return 'OK: ' + install.addon.id;"
        )
        result = current_bridge(ctx).execute_js(js, wait_seconds=10)
        if result.get("ok") and result.get("data", "").startswith("OK"):
            emit(ctx, {
                "action": "install_plugin",
                "method": "automatic",
                "plugin_path": str(xpi_path),
                "message": "Plugin installed. Restart Zotero so /cli-bridge/eval is active, then run: zotero-cli app plugin-status.",
            })
            return 0

    # Fallback: save xpi and instruct user to install manually
    emit(ctx, {
        "action": "install_plugin",
        "method": "manual",
        "plugin_path": str(xpi_path),
        "message": (
            "Plugin .xpi created. Install manually in Zotero: "
            "Tools > Add-ons > gear icon > Install Add-on From File, "
            f"then select: {xpi_path}. Restart Zotero so /cli-bridge/eval is active, "
            "then run: zotero-cli app plugin-status."
        ),
    })
    return 0


@app.command("plugin-status")
@click.pass_context
def app_plugin_status(ctx: click.Context) -> int:
    """Check if the CLI Bridge plugin is installed and the endpoint is active."""
    runtime = current_runtime(ctx)
    profile_dir = runtime.environment.profile_dir
    xpi_path = zotero_paths.plugin_xpi_path(profile_dir)
    installed = zotero_paths.plugin_installed(profile_dir)
    installed_version = zotero_paths.installed_plugin_version(profile_dir)
    bundled_version = zotero_paths.bundled_plugin_version()
    update_available = bool(installed_version and bundled_version and installed_version != bundled_version)
    active = current_bridge(ctx).bridge_endpoint_active()
    js_result = None
    js_ok = False
    js_error = None
    if active:
        result = current_bridge(ctx).execute_js_http_required("return {ok: true, value: 'cli-bridge-ok'};", wait_seconds=5)
        js_ok = bool(result.get("ok") and isinstance(result.get("data"), dict) and result["data"].get("value") == "cli-bridge-ok")
        js_result = result.get("data")
        js_error = result.get("error")
    next_step = "CLI Bridge is ready."
    if not installed:
        next_step = "Run: zotero-cli app install-plugin, restart Zotero, then rerun this command."
    elif update_available:
        next_step = "Run: zotero-cli app install-plugin, restart Zotero, then rerun this command."
    elif not active:
        next_step = "Restart Zotero. If the endpoint is still inactive, reinstall with: zotero-cli app install-plugin"
    elif not js_ok:
        next_step = "The endpoint responded, but eval did not return the expected value. Restart Zotero and rerun this command."
    emit(ctx, {
        "plugin": {
            "xpi_installed": installed,
            "xpi_path": str(xpi_path) if xpi_path else None,
            "profile_dir": str(profile_dir) if profile_dir else None,
            "installed_version": installed_version,
            "bundled_version": bundled_version,
            "update_available": update_available,
        },
        "bridge": {"endpoint_active": active, "js_ok": js_ok, "js_result": js_result, "js_error": js_error},
        "plugin_installed": installed,
        "endpoint_active": active,
        "profile_dir": str(profile_dir) if profile_dir else None,
        "ready": bool(installed and not update_available and active and js_ok),
        "next_step": next_step,
    })
    return 0


@app.command("check-update")
@click.pass_context
def app_check_update(ctx: click.Context) -> int:
    """Check if a newer version is available on GitHub."""
    import time
    import urllib.request
    from pathlib import Path

    cache_dir = Path("~/.config/cli-anything-zotero").expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "update-check.json"

    now = time.time()
    # Read cache — skip network if checked within 24h
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if now - cached.get("checked_at", 0) < 86400:
                if cached.get("update_available"):
                    emit(ctx, {
                        "update_available": True,
                        "current_version": __version__,
                        "latest_version": cached.get("latest_version", "unknown"),
                        "message": f"Update available: {__version__} -> {cached['latest_version']}. "
                                   f"Run: pip install -U https://github.com/PiaoyangGuohai1/cli-anything-zotero/archive/refs/heads/main.zip",
                    })
                    return 0
                # No update
                return 0
        except Exception:
            pass

    # Fetch latest version from GitHub
    try:
        url = "https://raw.githubusercontent.com/PiaoyangGuohai1/cli-anything-zotero/main/cli_anything/zotero/__init__.py"
        req = urllib.request.Request(url, headers={"User-Agent": "cli-anything-zotero"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
        latest = "unknown"
        for line in content.splitlines():
            if line.startswith("__version__"):
                latest = line.split("=", 1)[1].strip().strip("\"'")
                break
    except Exception:
        # Network error — skip silently
        return 0

    update_available = latest != "unknown" and latest != __version__
    cache_data = {
        "checked_at": now,
        "current_version": __version__,
        "latest_version": latest,
        "update_available": update_available,
    }
    try:
        cache_file.write_text(json.dumps(cache_data), encoding="utf-8")
    except Exception:
        pass

    if update_available:
        emit(ctx, {
            "update_available": True,
            "current_version": __version__,
            "latest_version": latest,
            "message": f"Update available: {__version__} -> {latest}. "
                       f"Run: pip install -U https://github.com/PiaoyangGuohai1/cli-anything-zotero/archive/refs/heads/main.zip",
        })
    else:
        emit(ctx, {
            "update_available": False,
            "current_version": __version__,
            "message": "You are on the latest version.",
        })
    return 0


@app.command("uninstall-plugin")
@click.pass_context
def app_uninstall_plugin(ctx: click.Context) -> int:
    """Remove the CLI Bridge plugin from Zotero (requires Zotero restart)."""
    runtime = current_runtime(ctx)
    profile_dir = runtime.environment.profile_dir
    if profile_dir is None:
        raise click.ClickException("Cannot resolve Zotero profile directory.")
    removed = zotero_paths.uninstall_plugin(profile_dir)
    emit(ctx, {
        "action": "uninstall_plugin",
        "removed": removed,
        "message": "Plugin removed. Restart Zotero." if removed else "Plugin was not installed.",
    })
    return 0


@cli.group()
def collection() -> None:
    """Collection inspection and selection commands."""


@collection.command("list")
@click.pass_context
def collection_list(ctx: click.Context) -> int:
    emit(ctx, catalog.list_collections(current_runtime(ctx), session=current_session()))
    return 0


@collection.command("find")
@click.argument("query")
@click.option("--limit", default=20, show_default=True, type=int)
@click.pass_context
def collection_find_command(ctx: click.Context, query: str, limit: int) -> int:
    emit(ctx, catalog.find_collections(current_runtime(ctx), query, limit=limit, session=current_session()))
    return 0


@collection.command("tree")
@click.pass_context
def collection_tree_command(ctx: click.Context) -> int:
    tree = catalog.collection_tree(current_runtime(ctx), session=current_session())
    if root_json_output(ctx):
        emit(ctx, tree)
    else:
        _print_collection_tree(tree)
    return 0


@collection.command("get")
@click.argument("ref", required=False)
@click.pass_context
def collection_get(ctx: click.Context, ref: str | None) -> int:
    emit(ctx, catalog.get_collection(current_runtime(ctx), ref, session=current_session()))
    return 0


@collection.command("items")
@click.argument("ref", required=False)
@click.pass_context
def collection_items_command(ctx: click.Context, ref: str | None) -> int:
    emit(ctx, catalog.collection_items(current_runtime(ctx), ref, session=current_session()))
    return 0


def _persist_selected_collection(selected: dict[str, Any]) -> dict[str, Any]:
    state = current_session()
    state["current_library"] = selected.get("libraryID")
    state["current_collection"] = selected.get("id")
    session_mod.save_session_state(state)
    return state


@collection.command("use-selected")
@click.pass_context
def collection_use_selected(ctx: click.Context) -> int:
    selected = catalog.use_selected_collection(current_runtime(ctx))
    _persist_selected_collection(selected)
    session_mod.append_command_history("collection use-selected")
    emit(ctx, selected)
    return 0


@collection.command("create")
@click.argument("name")
@click.option("--parent", "parent_ref", default=None, help="Parent collection key.")
@click.option("--experimental", "experimental_mode", is_flag=True, help="Force experimental direct SQLite write mode (requires Zotero closed).")
@click.pass_context
def collection_create_command(
    ctx: click.Context,
    name: str,
    parent_ref: str | None,
    experimental_mode: bool,
) -> int:
    runtime = current_runtime(ctx)
    if experimental_mode:
        emit(
            ctx,
            experimental.create_collection(
                runtime, name, parent_ref=parent_ref, session=current_session(),
            ),
        )
    else:
        # Prefer JS Bridge (works while Zotero is running)
        result = current_bridge(ctx).create_collection(
            name, parent_key=parent_ref, library_id=int(current_session().get("current_library", 1)),
        )
        if not result.get("ok", True):
            raise RuntimeError(result.get("error", "Failed to create collection"))
        data = result.get("data", result)
        if isinstance(data, dict):
            data["action"] = "collection_create"
            emit(ctx, data)
        else:
            emit(ctx, result, message=f"OK: created collection {name}")
    return 0


@collection.command("find-pdfs")
@click.argument("collection_key")
@click.pass_context
def collection_find_pdfs_command(ctx: click.Context, collection_key: str) -> int:
    """Find available PDFs for all items missing PDFs in a collection (via JS bridge)."""
    result = current_bridge(ctx).find_pdfs_in_collection(collection_key)
    return emit_js(ctx, result)


@collection.command("stats")
@click.argument("collection_key")
@click.pass_context
def collection_stats_command(ctx: click.Context, collection_key: str) -> int:
    """Get statistics for a Zotero collection (via JS bridge)."""
    result = current_bridge(ctx).collection_stats(collection_key)
    return emit_js(ctx, result)


@collection.command("remove-item")
@click.argument("collection_key")
@click.argument("item_key")
@click.pass_context
def collection_remove_item_command(ctx: click.Context, collection_key: str, item_key: str) -> int:
    """Remove an item from a collection (item is NOT deleted)."""
    result = current_bridge(ctx).remove_from_collection(item_key, collection_key)
    return emit_js(ctx, result)


@collection.command("delete")
@click.argument("collection_key")
@click.option("--delete-items", is_flag=True, help="Also delete all items in the collection.")
@click.option("--confirm", is_flag=True, required=True, help="Required to confirm deletion.")
@click.pass_context
def collection_delete_command(ctx: click.Context, collection_key: str, delete_items: bool, confirm: bool) -> int:
    """Delete a collection (via JS bridge). Requires --confirm."""
    result = current_bridge(ctx).delete_collection(collection_key, delete_items=delete_items)
    return emit_js(ctx, result)


@collection.command("rename")
@click.argument("collection_key")
@click.option("--name", help="New collection name.")
@click.option("--parent", "parent_key", help="Move under this parent collection key.")
@click.pass_context
def collection_rename_command(ctx: click.Context, collection_key: str, name: str | None, parent_key: str | None) -> int:
    """Rename or move a collection (via JS bridge)."""
    result = current_bridge(ctx).update_collection(collection_key, name=name, parent_key=parent_key)
    return emit_js(ctx, result)


@cli.command("js")
@click.argument("code")
@click.option("--wait", default=3, help="Seconds to wait for execution.")
@click.pass_context
def js_command(ctx: click.Context, code: str, wait: int) -> int:
    """Execute arbitrary JavaScript in Zotero's JS console (via JS bridge)."""
    result = current_bridge(ctx).execute_js(code, wait_seconds=wait)
    return emit_js(ctx, result)


@cli.command("sync")
@click.pass_context
def sync_command(ctx: click.Context) -> int:
    """Trigger a Zotero sync operation (via JS bridge)."""
    result = current_bridge(ctx).trigger_sync()
    return emit_js(ctx, result)


@cli.group()
def item() -> None:
    """Item inspection and rendering commands."""


@item.command("list")
@click.option("--limit", default=20, show_default=True, type=int)
@click.pass_context
def item_list(ctx: click.Context, limit: int) -> int:
    emit(ctx, catalog.list_items(current_runtime(ctx), session=current_session(), limit=limit))
    return 0


@item.command("find")
@click.argument("query")
@click.option("--collection", "collection_ref", default=None, help="Collection ID or key scope.")
@click.option("--limit", default=20, show_default=True, type=int)
@click.option("--exact-title", is_flag=True, help="Use exact title matching via SQLite.")
@click.pass_context
def item_find_command(
    ctx: click.Context,
    query: str,
    collection_ref: str | None,
    limit: int,
    exact_title: bool,
) -> int:
    emit(
        ctx,
        catalog.find_items(
            current_runtime(ctx),
            query,
            collection_ref=collection_ref,
            limit=limit,
            exact_title=exact_title,
            session=current_session(),
        ),
    )
    return 0


@item.command("get")
@click.argument("ref", required=False)
@click.pass_context
def item_get(ctx: click.Context, ref: str | None) -> int:
    emit(ctx, catalog.get_item(current_runtime(ctx), ref, session=current_session()))
    return 0


@item.command("children")
@click.argument("ref", required=False)
@click.pass_context
def item_children_command(ctx: click.Context, ref: str | None) -> int:
    emit(ctx, catalog.item_children(current_runtime(ctx), ref, session=current_session()))
    return 0


@item.command("notes")
@click.argument("ref", required=False)
@click.pass_context
def item_notes_command(ctx: click.Context, ref: str | None) -> int:
    emit(ctx, catalog.item_notes(current_runtime(ctx), ref, session=current_session()))
    return 0


@item.command("attachments")
@click.argument("ref", required=False)
@click.pass_context
def item_attachments_command(ctx: click.Context, ref: str | None) -> int:
    emit(ctx, catalog.item_attachments(current_runtime(ctx), ref, session=current_session()))
    return 0


@item.command("file")
@click.argument("ref", required=False)
@click.pass_context
def item_file_command(ctx: click.Context, ref: str | None) -> int:
    emit(ctx, catalog.item_file(current_runtime(ctx), ref, session=current_session()))
    return 0


@item.command("attach")
@click.argument("item_key")
@click.argument("pdf_path", type=click.Path(exists=True))
@click.pass_context
def item_attach_command(ctx: click.Context, item_key: str, pdf_path: str) -> int:
    """Attach a local PDF file to an existing Zotero item (via JS bridge)."""
    result = current_bridge(ctx).attach_pdf(item_key, pdf_path)
    return emit_js(ctx, result)


@item.command("find-pdf")
@click.argument("item_key")
@click.option("--timeout", default=30, help="Seconds to wait for PDF download (default: 30).")
@click.pass_context
def item_find_pdf_command(ctx: click.Context, item_key: str, timeout: int) -> int:
    """Trigger Zotero's 'Find Available PDF' for a single item (via JS bridge)."""
    result = current_bridge(ctx).find_pdf(item_key, timeout=timeout)
    return emit_js(ctx, result)


@item.command("search-annotations")
@click.argument("query", default="")
@click.option("--color", "colors", multiple=True, help="Filter by annotation color (repeatable). E.g. yellow, red, #ffd400")
@click.option("--limit", default=20, help="Max results.")
@click.pass_context
def item_search_annotations_command(ctx: click.Context, query: str, colors: tuple, limit: int) -> int:
    """Search annotations across all items by keyword and/or color."""
    result = current_bridge(ctx).search_annotations(query, colors=list(colors) if colors else None, limit=limit)
    return emit_js(ctx, result)


@item.command("semantic-search")
@click.argument("query")
@click.option("--top-k", default=10, help="Number of results.")
@click.option("--min-score", default=0.3, help="Minimum similarity score (0-1).")
@click.option("--language", type=click.Choice(["zh", "en", "all"]), default="all")
@click.pass_context
def item_semantic_search_command(ctx: click.Context, query: str, top_k: int, min_score: float, language: str) -> int:
    """Semantic search across Zotero library using local embedding model."""
    result = semantic.semantic_search(query, top_k=top_k, min_score=min_score, language=language)
    return emit_js(ctx, result)


@item.command("similar")
@click.argument("item_key")
@click.option("--top-k", default=5, help="Number of similar items.")
@click.option("--min-score", default=0.5, help="Minimum similarity score (0-1).")
@click.pass_context
def item_similar_command(ctx: click.Context, item_key: str, top_k: int, min_score: float) -> int:
    """Find items similar to a given item using embeddings."""
    result = semantic.find_similar(item_key, top_k=top_k, min_score=min_score)
    return emit_js(ctx, result)


@item.command("build-index")
@click.pass_context
def item_build_index_command(ctx: click.Context) -> int:
    """Build the semantic search vector index from your Zotero library."""
    runtime = current_runtime(ctx)
    result = semantic.build_index(str(runtime.environment.sqlite_path))
    return emit_js(ctx, result)


@item.command("update")
@click.argument("item_key")
@click.option("--field", "fields", multiple=True, help="Field to update as key=value. Repeatable.")
@click.pass_context
def item_update_command(ctx: click.Context, item_key: str, fields: tuple[str, ...]) -> int:
    """Update metadata fields on an existing Zotero item (via JS bridge)."""
    fields_dict: dict[str, str] = {}
    for f in fields:
        if "=" not in f:
            raise click.ClickException(f"Invalid field format (expected key=value): {f}")
        k, v = f.split("=", 1)
        fields_dict[k.strip()] = v.strip()
    if not fields_dict:
        raise click.ClickException("At least one --field key=value is required.")
    result = current_bridge(ctx).update_item_fields(item_key, fields_dict)
    return emit_js(ctx, result)


@item.command("tag")
@click.argument("item_key")
@click.option("--add", "add_tags", multiple=True, help="Tag to add. Repeatable.")
@click.option("--remove", "remove_tags", multiple=True, help="Tag to remove. Repeatable.")
@click.pass_context
def item_tag_command(ctx: click.Context, item_key: str, add_tags: tuple[str, ...], remove_tags: tuple[str, ...]) -> int:
    """Add or remove tags on an existing Zotero item (via JS bridge)."""
    if not add_tags and not remove_tags:
        raise click.ClickException("At least one --add or --remove tag is required.")
    result = current_bridge(ctx).manage_tags(item_key, list(add_tags), list(remove_tags))
    return emit_js(ctx, result)


@item.command("export")
@click.argument("ref", required=False)
@click.option("--format", "fmt", type=click.Choice(list(rendering.SUPPORTED_EXPORT_FORMATS)), required=True)
@click.pass_context
def item_export(ctx: click.Context, ref: str | None, fmt: str) -> int:
    payload = rendering.export_item(current_runtime(ctx), ref, fmt, session=current_session())
    emit(ctx, payload if root_json_output(ctx) else payload["content"])
    return 0


@item.command("citation")
@click.argument("ref", required=False)
@click.option("--style", default=None)
@click.option("--locale", default=None)
@click.option("--linkwrap", is_flag=True)
@click.pass_context
def item_citation(ctx: click.Context, ref: str | None, style: str | None, locale: str | None, linkwrap: bool) -> int:
    payload = rendering.citation_item(current_runtime(ctx), ref, style=style, locale=locale, linkwrap=linkwrap, session=current_session())
    emit(ctx, payload if root_json_output(ctx) else (payload.get("citation") or ""))
    return 0


@item.command("bibliography")
@click.argument("ref", required=False)
@click.option("--style", default=None)
@click.option("--locale", default=None)
@click.option("--linkwrap", is_flag=True)
@click.pass_context
def item_bibliography(ctx: click.Context, ref: str | None, style: str | None, locale: str | None, linkwrap: bool) -> int:
    payload = rendering.bibliography_item(current_runtime(ctx), ref, style=style, locale=locale, linkwrap=linkwrap, session=current_session())
    emit(ctx, payload if root_json_output(ctx) else (payload.get("bibliography") or ""))
    return 0


@item.command("context")
@click.argument("ref", required=False)
@click.option("--include-notes", is_flag=True)
@click.option("--include-bibtex", is_flag=True)
@click.option("--include-csljson", is_flag=True)
@click.option("--include-links", is_flag=True)
@click.pass_context
def item_context_command(
    ctx: click.Context,
    ref: str | None,
    include_notes: bool,
    include_bibtex: bool,
    include_csljson: bool,
    include_links: bool,
) -> int:
    payload = analysis.build_item_context(
        current_runtime(ctx),
        ref,
        include_notes=include_notes,
        include_bibtex=include_bibtex,
        include_csljson=include_csljson,
        include_links=include_links,
        session=current_session(),
    )
    emit(ctx, payload if root_json_output(ctx) else payload["prompt_context"])
    return 0


@item.command("analyze")
@click.argument("ref", required=False)
@click.option("--question", required=True)
@click.option("--model", required=True)
@click.option("--include-notes", is_flag=True)
@click.option("--include-bibtex", is_flag=True)
@click.option("--include-csljson", is_flag=True)
@click.pass_context
def item_analyze_command(
    ctx: click.Context,
    ref: str | None,
    question: str,
    model: str,
    include_notes: bool,
    include_bibtex: bool,
    include_csljson: bool,
) -> int:
    payload = analysis.analyze_item(
        current_runtime(ctx),
        ref,
        question=question,
        model=model,
        include_notes=include_notes,
        include_bibtex=include_bibtex,
        include_csljson=include_csljson,
        session=current_session(),
    )
    emit(ctx, payload if root_json_output(ctx) else payload["answer"])
    return 0


@item.command("add-to-collection")
@click.argument("item_ref")
@click.argument("collection_ref")
@click.option("--experimental", "experimental_mode", is_flag=True, help="Force experimental direct SQLite write mode (Zotero must be closed).")
@click.pass_context
def item_add_to_collection_command(ctx: click.Context, item_ref: str, collection_ref: str, experimental_mode: bool) -> int:
    runtime = current_runtime(ctx)
    if experimental_mode:
        emit(ctx, experimental.add_item_to_collection(runtime, item_ref, collection_ref, session=current_session()))
    else:
        result = current_bridge(ctx).add_to_collection(item_ref, collection_ref)
        emit_js(ctx, result)
    return 0


@item.command("move-to-collection")
@click.argument("item_ref")
@click.argument("collection_ref")
@click.option("--from", "from_refs", multiple=True, help="Source collection ID or key. Repeatable.")
@click.option("--all-other-collections", is_flag=True, help="Remove the item from all other collections after adding the target.")
@click.option("--experimental", "experimental_mode", is_flag=True, help="Acknowledge experimental direct SQLite write mode.")
@click.pass_context
def item_move_to_collection_command(
    ctx: click.Context,
    item_ref: str,
    collection_ref: str,
    from_refs: tuple[str, ...],
    all_other_collections: bool,
    experimental_mode: bool,
) -> int:
    _require_experimental_flag(experimental_mode, "item move-to-collection")
    emit(
        ctx,
        experimental.move_item_to_collection(
            current_runtime(ctx),
            item_ref,
            collection_ref,
            from_refs=list(from_refs),
            all_other_collections=all_other_collections,
            session=current_session(),
        ),
    )
    return 0


@item.command("search-fulltext")
@click.argument("query")
@click.option("--limit", default=10, show_default=True, type=int, help="Maximum number of results.")
@click.pass_context
def item_search_fulltext_command(ctx: click.Context, query: str, limit: int) -> int:
    """Search full-text content of PDFs in the Zotero library (via JS bridge)."""
    result = current_bridge(ctx).search_fulltext(query, limit=limit)
    return emit_js(ctx, result)


@item.command("annotations")
@click.argument("item_key")
@click.pass_context
def item_annotations_command(ctx: click.Context, item_key: str) -> int:
    """View annotations and highlights for a Zotero item (via JS bridge)."""
    result = current_bridge(ctx).get_annotations(item_key)
    return emit_js(ctx, result)


@item.command("metrics")
@click.argument("ref")
@click.option("--pmid", "is_pmid", is_flag=True, help="Treat REF as a PMID directly instead of a Zotero item key.")
@click.pass_context
def item_metrics_command(ctx: click.Context, ref: str, is_pmid: bool) -> int:
    """Fetch NIH iCite citation metrics for an item (by PMID or Zotero item key)."""
    if is_pmid:
        pmid = ref
    else:
        # Look up the item by key and extract PMID from the extra field
        try:
            item_data = catalog.get_item(current_runtime(ctx), ref, session=current_session())
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        fields = item_data.get("fields", {})
        pmid = None
        # Check direct PMID field first (Zotero 7+ stores PMID as a dedicated field)
        if fields.get("PMID"):
            pmid = str(fields["PMID"]).strip()
        else:
            # Fallback: parse PMID from the extra field text
            extra = fields.get("extra") or ""
            for line in extra.splitlines():
                stripped = line.strip()
                if stripped.upper().startswith("PMID:"):
                    pmid = stripped.split(":", 1)[1].strip()
                    break
        if not pmid:
            raise click.ClickException(
                f"No PMID found in item '{ref}' (checked PMID field and extra text). "
                "Use --pmid flag to pass a PMID directly."
            )
    result = metrics.get_metrics(pmid)
    emit(ctx, result)
    return 1 if "error" in result else 0


@item.command("delete")
@click.argument("item_key")
@click.option("--confirm", is_flag=True, help="Confirm deletion. Required to prevent accidental deletions.")
@click.pass_context
def item_delete_command(ctx: click.Context, item_key: str, confirm: bool) -> int:
    """Delete a Zotero item permanently (via JS bridge)."""
    if not confirm:
        raise click.ClickException(
            f"Deleting item '{item_key}' is irreversible. "
            "Pass --confirm to proceed."
        )
    result = current_bridge(ctx).delete_item(item_key)
    return emit_js(ctx, result)


@item.command("duplicates")
@click.option("--limit", default=50, show_default=True, type=int, help="Maximum number of duplicates to return.")
@click.pass_context
def item_duplicates_command(ctx: click.Context, limit: int) -> int:
    """Find duplicate items in the Zotero library (via JS bridge)."""
    result = current_bridge(ctx).find_duplicates(limit=limit)
    return emit_js(ctx, result)


@cli.group()
def search() -> None:
    """Saved-search inspection commands."""


@search.command("list")
@click.pass_context
def search_list(ctx: click.Context) -> int:
    emit(ctx, catalog.list_searches(current_runtime(ctx), session=current_session()))
    return 0


@search.command("get")
@click.argument("ref")
@click.pass_context
def search_get(ctx: click.Context, ref: str) -> int:
    emit(ctx, catalog.get_search(current_runtime(ctx), ref, session=current_session()))
    return 0


@search.command("items")
@click.argument("ref")
@click.pass_context
def search_items_command(ctx: click.Context, ref: str) -> int:
    emit(ctx, catalog.search_items(current_runtime(ctx), ref, session=current_session()))
    return 0


@cli.group()
def tag() -> None:
    """Tag inspection commands."""


@tag.command("list")
@click.pass_context
def tag_list(ctx: click.Context) -> int:
    emit(ctx, catalog.list_tags(current_runtime(ctx), session=current_session()))
    return 0


@tag.command("items")
@click.argument("tag_ref")
@click.pass_context
def tag_items_command(ctx: click.Context, tag_ref: str) -> int:
    emit(ctx, catalog.tag_items(current_runtime(ctx), tag_ref, session=current_session()))
    return 0


@cli.group()
def style() -> None:
    """Installed CSL style inspection commands."""


@style.command("list")
@click.pass_context
def style_list(ctx: click.Context) -> int:
    emit(ctx, catalog.list_styles(current_runtime(ctx)))
    return 0


@cli.group()
def docx() -> None:
    """DOCX citation inspection commands."""


@docx.command("inspect-citations")
@click.argument("path")
@click.option("--sample-limit", default=10, show_default=True, type=int, help="Maximum field/static citation samples to include.")
@click.pass_context
def docx_inspect_citations(ctx: click.Context, path: str, sample_limit: int) -> int:
    """Inspect a DOCX file for Zotero, EndNote, CSL, and static citations."""
    try:
        payload = docx_tools.inspect_citations(path, sample_limit=sample_limit)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


@docx.command("inspect-placeholders")
@click.argument("path")
@click.option("--sample-limit", default=10, show_default=True, type=int, help="Maximum placeholder samples to include.")
@click.pass_context
def docx_inspect_placeholders(ctx: click.Context, path: str, sample_limit: int) -> int:
    """Inspect DOCX Zotero placeholders such as {{zotero:ITEMKEY}}."""
    try:
        payload = docx_tools.inspect_placeholders(path, sample_limit=sample_limit)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


@docx.command("validate-placeholders")
@click.argument("path")
@click.option("--sample-limit", default=10, show_default=True, type=int, help="Maximum placeholder samples to include.")
@click.pass_context
def docx_validate_placeholders(ctx: click.Context, path: str, sample_limit: int) -> int:
    """Validate DOCX Zotero placeholders against the local Zotero database."""
    try:
        payload = docx_tools.validate_placeholders(current_runtime(ctx), path, sample_limit=sample_limit, session=current_session())
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


@docx.command("zoterify-preflight")
@click.argument("path")
@click.option("--sample-limit", default=10, show_default=True, type=int, help="Maximum placeholder samples to include.")
@click.option("--skip-external-checks", is_flag=True, help="Only validate DOCX placeholders and local Zotero item resolution.")
@click.pass_context
def docx_zoterify_preflight(ctx: click.Context, path: str, sample_limit: int, skip_external_checks: bool) -> int:
    """Check whether a placeholder DOCX is ready for Zotero/LibreOffice conversion."""
    try:
        payload = docx_tools.zoterify_preflight(
            current_runtime(ctx),
            path,
            sample_limit=sample_limit,
            session=current_session(),
            check_external=not skip_external_checks,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


@docx.command("prepare-zotero-import")
@click.argument("path")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Output transfer .docx path.")
@click.option("--style", default="http://www.zotero.org/styles/apa", show_default=True, help="CSL style ID for the Zotero document preferences.")
@click.option("--locale", default="en-US", show_default=True, help="CSL locale for the Zotero document preferences.")
@click.option("--sample-limit", default=10, show_default=True, type=int, help="Maximum placeholder samples to include in validation.")
@click.option("--skip-external-checks", is_flag=True, help="Only validate DOCX placeholders and local Zotero item resolution.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it already exists.")
@click.option("--experimental", is_flag=True, help="Enable the unstable Zotero transfer-DOCX experiment.")
@click.pass_context
def docx_prepare_zotero_import(
    ctx: click.Context,
    path: str,
    output: Path,
    style: str,
    locale: str,
    sample_limit: int,
    skip_external_checks: bool,
    force: bool,
    experimental: bool,
) -> int:
    """Create an experimental Zotero transfer DOCX from {{zotero:ITEMKEY}} placeholders."""
    if not experimental:
        raise click.ClickException(
            "prepare-zotero-import is experimental and has failed in Zotero 9 + LibreOffice testing. "
            "Pass --experimental only when debugging the transfer-DOCX path."
        )
    try:
        payload = docx_tools.prepare_zotero_import_document(
            current_runtime(ctx),
            path,
            output,
            style=style,
            locale=locale,
            sample_limit=sample_limit,
            session=current_session(),
            check_external=not skip_external_checks,
            overwrite=force,
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


@docx.command("zoterify-probe")
@click.option("--backend", default=docx_zoterify.DEFAULT_BACKEND, show_default=True, help="Word processor backend.")
@click.pass_context
def docx_zoterify_probe(ctx: click.Context, backend: str) -> int:
    """Probe whether Zotero and LibreOffice are ready for DOCX zoterify."""
    try:
        payload = docx_zoterify.zoterify_probe(current_bridge(ctx), backend=backend)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


@docx.command("doctor")
@click.option("--backend", default=docx_zoterify.DEFAULT_BACKEND, show_default=True, help="Word processor backend.")
@click.pass_context
def docx_doctor(ctx: click.Context, backend: str) -> int:
    """Check optional LibreOffice-backed dynamic DOCX citation requirements."""
    try:
        payload = docx_zoterify.zoterify_doctor(current_runtime(ctx), current_bridge(ctx), backend=backend)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


@docx.command("zoterify")
@click.argument("path")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Output .docx path.")
@click.option("--backend", default=docx_zoterify.DEFAULT_BACKEND, show_default=True, help="Word processor backend.")
@click.option("--style", default=docx_zoterify.DEFAULT_STYLE, show_default=True, help="CSL style ID or short style name.")
@click.option("--locale", default=docx_zoterify.DEFAULT_LOCALE, show_default=True, help="CSL locale.")
@click.option("--field-type", default=docx_zoterify.DEFAULT_FIELD_TYPE, show_default=True, help="LibreOffice field type.")
@click.option("--bibliography", type=click.Choice(["auto", "none"]), default=docx_zoterify.DEFAULT_BIBLIOGRAPHY, show_default=True, help="Whether to create/update a Zotero bibliography field.")
@click.option("--open/--no-open", "open_document", default=True, show_default=True, help="Attempt to open the output DOCX in LibreOffice before conversion.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it already exists.")
@click.option("--debug-dir", type=click.Path(file_okay=False, path_type=Path), help="Optional directory for zoterify debug JSON artifacts.")
@click.pass_context
def docx_zoterify_command(
    ctx: click.Context,
    path: str,
    output: Path,
    backend: str,
    style: str,
    locale: str,
    field_type: str,
    bibliography: str,
    open_document: bool,
    force: bool,
    debug_dir: Path | None,
) -> int:
    """Convert {{zotero:ITEMKEY}} placeholders into Zotero LibreOffice fields."""
    return _run_docx_zoterify(ctx, path, output, backend, style, locale, field_type, bibliography, open_document, force, debug_dir)


@docx.command("insert-citations")
@click.argument("path")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Output .docx path.")
@click.option("--backend", default=docx_zoterify.DEFAULT_BACKEND, show_default=True, help="Word processor backend.")
@click.option("--style", default=docx_zoterify.DEFAULT_STYLE, show_default=True, help="CSL style ID or short style name.")
@click.option("--locale", default=docx_zoterify.DEFAULT_LOCALE, show_default=True, help="CSL locale.")
@click.option("--field-type", default=docx_zoterify.DEFAULT_FIELD_TYPE, show_default=True, help="LibreOffice field type.")
@click.option("--bibliography", type=click.Choice(["auto", "none"]), default=docx_zoterify.DEFAULT_BIBLIOGRAPHY, show_default=True, help="Whether to create/update a Zotero bibliography field.")
@click.option("--open/--no-open", "open_document", default=True, show_default=True, help="Attempt to open the output DOCX in LibreOffice before conversion.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it already exists.")
@click.option("--debug-dir", type=click.Path(file_okay=False, path_type=Path), help="Optional directory for zoterify debug JSON artifacts.")
@click.pass_context
def docx_insert_citations_command(
    ctx: click.Context,
    path: str,
    output: Path,
    backend: str,
    style: str,
    locale: str,
    field_type: str,
    bibliography: str,
    open_document: bool,
    force: bool,
    debug_dir: Path | None,
) -> int:
    """AI-friendly alias for converting Zotero placeholders into final citation fields."""
    return _run_docx_zoterify(ctx, path, output, backend, style, locale, field_type, bibliography, open_document, force, debug_dir)


@docx.command("render-citations")
@click.argument("path")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Output .docx path.")
@click.option("--style", default=docx_static.DEFAULT_STYLE, show_default=True, help="CSL style ID or short style name.")
@click.option("--locale", default=docx_static.DEFAULT_LOCALE, show_default=True, help="CSL locale.")
@click.option("--bibliography", type=click.Choice(["auto", "none"]), default=docx_static.DEFAULT_BIBLIOGRAPHY, show_default=True, help="Whether to append a static bibliography.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it already exists.")
@click.pass_context
def docx_render_citations_command(
    ctx: click.Context,
    path: str,
    output: Path,
    style: str,
    locale: str,
    bibliography: str,
    force: bool,
) -> int:
    """Convert Zotero placeholders into static citation and bibliography text."""
    try:
        payload = docx_static.render_static_citations(
            current_runtime(ctx),
            path,
            output,
            style=style,
            locale=locale,
            bibliography=bibliography,
            session=current_session(),
            overwrite=force,
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


def _run_docx_zoterify(
    ctx: click.Context,
    path: str,
    output: Path,
    backend: str,
    style: str,
    locale: str,
    field_type: str,
    bibliography: str,
    open_document: bool,
    force: bool,
    debug_dir: Path | None,
) -> int:
    try:
        payload = docx_zoterify.zoterify_document(
            current_runtime(ctx),
            current_bridge(ctx),
            path,
            output,
            backend=backend,
            style=style,
            locale=locale,
            field_type=field_type,
            bibliography=bibliography,
            session=current_session(),
            open_document=open_document,
            overwrite=force,
            debug_dir=debug_dir,
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    emit(ctx, payload)
    return 0


@cli.group("export")
def export_group() -> None:
    """Independent Zotero data export commands."""


@export_group.command("bib")
@click.option("--items", default=None, help="Comma-separated item keys/IDs to export.")
@click.option("--collection", "collection_ref", default=None, help="Collection key/ID whose top-level items should be exported.")
@click.option("--format", "fmt", type=click.Choice(["bibtex", "biblatex"]), default="bibtex", show_default=True)
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Output .bib file path.")
@click.pass_context
def export_bib_command(ctx: click.Context, items: str | None, collection_ref: str | None, fmt: str, output: Path) -> int:
    """Export real Zotero items to a standalone BibTeX/BibLaTeX file."""
    if bool(items) == bool(collection_ref):
        raise click.ClickException("Pass exactly one of --items or --collection.")

    runtime = current_runtime(ctx)
    session = current_session()
    if items:
        refs = _split_export_refs(items)
        source: dict[str, Any] = {"type": "items", "refs": refs}
    else:
        collection = catalog.get_collection(runtime, collection_ref, session=session)
        collection_items = [
            item
            for item in catalog.collection_items(runtime, collection_ref, session=session)
            if not item.get("isAttachment") and not item.get("isNote") and not item.get("isAnnotation")
        ]
        refs = [str(item["key"]) for item in collection_items]
        source = {"type": "collection", "collection": collection}

    if not refs:
        raise click.ClickException("No exportable Zotero items found.")

    try:
        exported = [rendering.export_item(runtime, ref, fmt, session=session) for ref in refs]
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    content = "\n\n".join(entry["content"].strip() for entry in exported if entry.get("content"))
    output.write_text(content + ("\n" if content else ""), encoding="utf-8")
    emit(
        ctx,
        {
            "action": "export-bib",
            "format": fmt,
            "output": str(output),
            "item_count": len(exported),
            "items": [{"itemKey": entry["itemKey"], "libraryID": entry["libraryID"]} for entry in exported],
            "source": source,
        },
    )
    return 0


def _split_export_refs(items: str) -> list[str]:
    refs = [part.strip() for part in items.split(",") if part.strip()]
    if not refs:
        raise click.ClickException("--items must contain at least one item key or ID.")
    return refs


@cli.group("import")
def import_group() -> None:
    """Official Zotero import and write commands."""


@import_group.command("file")
@click.argument("path")
@click.option("--collection", "collection_ref", default=None, help="Collection ID, key, or treeViewID target.")
@click.option("--tag", "tags", multiple=True, help="Tag to apply after import. Repeatable.")
@click.option("--attachments-manifest", default=None, help="Optional JSON manifest describing attachments for imported records.")
@click.option("--attachment-delay-ms", default=0, show_default=True, type=int, help="Default delay before each URL attachment download.")
@click.option("--attachment-timeout", default=60, show_default=True, type=int, help="Default timeout in seconds for attachment download/upload.")
@click.pass_context
def import_file_command(
    ctx: click.Context,
    path: str,
    collection_ref: str | None,
    tags: tuple[str, ...],
    attachments_manifest: str | None,
    attachment_delay_ms: int,
    attachment_timeout: int,
) -> int:
    payload = imports.import_file(
        current_runtime(ctx),
        path,
        collection_ref=collection_ref,
        tags=list(tags),
        session=current_session(),
        attachments_manifest=attachments_manifest,
        attachment_delay_ms=attachment_delay_ms,
        attachment_timeout=attachment_timeout,
    )
    emit(ctx, payload)
    return _import_exit_code(payload)


@import_group.command("json")
@click.argument("path")
@click.option("--collection", "collection_ref", default=None, help="Collection ID, key, or treeViewID target.")
@click.option("--tag", "tags", multiple=True, help="Tag to apply after import. Repeatable.")
@click.option("--attachment-delay-ms", default=0, show_default=True, type=int, help="Default delay before each URL attachment download.")
@click.option("--attachment-timeout", default=60, show_default=True, type=int, help="Default timeout in seconds for attachment download/upload.")
@click.pass_context
def import_json_command(
    ctx: click.Context,
    path: str,
    collection_ref: str | None,
    tags: tuple[str, ...],
    attachment_delay_ms: int,
    attachment_timeout: int,
) -> int:
    payload = imports.import_json(
        current_runtime(ctx),
        path,
        collection_ref=collection_ref,
        tags=list(tags),
        session=current_session(),
        attachment_delay_ms=attachment_delay_ms,
        attachment_timeout=attachment_timeout,
    )
    emit(ctx, payload)
    return _import_exit_code(payload)


@import_group.command("doi")
@click.argument("doi")
@click.option("--collection", "collection_key", default=None, help="Collection key to add the imported item to.")
@click.option("--tag", "tags", multiple=True, help="Tag to apply after import. Repeatable.")
@click.pass_context
def import_doi_command(ctx: click.Context, doi: str, collection_key: str | None, tags: tuple[str, ...]) -> int:
    """Import an item by DOI using Zotero's built-in translator (via JS bridge)."""
    result = current_bridge(ctx).import_from_doi(doi, collection_key=collection_key, tags=list(tags) if tags else None)
    return emit_js(ctx, result)


@import_group.command("pmid")
@click.argument("pmid")
@click.option("--collection", "collection_key", default=None, help="Collection key to add the imported item to.")
@click.option("--tag", "tags", multiple=True, help="Tag to apply after import. Repeatable.")
@click.pass_context
def import_pmid_command(ctx: click.Context, pmid: str, collection_key: str | None, tags: tuple[str, ...]) -> int:
    """Import an item by PMID using Zotero's built-in translator (via JS bridge)."""
    result = current_bridge(ctx).import_from_pmid(pmid, collection_key=collection_key, tags=list(tags) if tags else None)
    return emit_js(ctx, result)


@cli.group()
def note() -> None:
    """Read and add child notes."""


@note.command("get")
@click.argument("ref")
@click.pass_context
def note_get_command(ctx: click.Context, ref: str) -> int:
    payload = notes.get_note(current_runtime(ctx), ref, session=current_session())
    emit(ctx, payload if root_json_output(ctx) else (payload.get("noteText") or payload.get("noteContent") or ""))
    return 0


@note.command("add")
@click.argument("item_ref")
@click.option("--text", default=None, help="Inline note content.")
@click.option("--file", "file_path", default=None, help="Read note content from a file.")
@click.option("--format", "fmt", type=click.Choice(["text", "markdown", "html"]), default="text", show_default=True)
@click.pass_context
def note_add_command(
    ctx: click.Context,
    item_ref: str,
    text: str | None,
    file_path: str | None,
    fmt: str,
) -> int:
    emit(
        ctx,
        notes.add_note(
            current_runtime(ctx),
            item_ref,
            text=text,
            file_path=file_path,
            fmt=fmt,
            session=current_session(),
        ),
    )
    return 0


@cli.group()
def session() -> None:
    """Session and REPL context commands."""


@session.command("status")
@click.pass_context
def session_status(ctx: click.Context) -> int:
    emit(ctx, session_mod.build_session_payload(current_session()))
    return 0


@session.command("use-library")
@click.argument("library_ref")
@click.pass_context
def session_use_library(ctx: click.Context, library_ref: str) -> int:
    state = current_session()
    state["current_library"] = _normalize_session_library(current_runtime(ctx), library_ref)
    session_mod.save_session_state(state)
    session_mod.append_command_history(f"session use-library {library_ref}")
    emit(ctx, session_mod.build_session_payload(state))
    return 0


@session.command("use-collection")
@click.argument("collection_ref")
@click.pass_context
def session_use_collection(ctx: click.Context, collection_ref: str) -> int:
    state = current_session()
    state["current_collection"] = collection_ref
    session_mod.save_session_state(state)
    session_mod.append_command_history(f"session use-collection {collection_ref}")
    emit(ctx, session_mod.build_session_payload(state))
    return 0


@session.command("use-item")
@click.argument("item_ref")
@click.pass_context
def session_use_item(ctx: click.Context, item_ref: str) -> int:
    state = current_session()
    state["current_item"] = item_ref
    session_mod.save_session_state(state)
    session_mod.append_command_history(f"session use-item {item_ref}")
    emit(ctx, session_mod.build_session_payload(state))
    return 0


@session.command("use-selected")
@click.pass_context
def session_use_selected(ctx: click.Context) -> int:
    selected = catalog.use_selected_collection(current_runtime(ctx))
    state = _persist_selected_collection(selected)
    session_mod.append_command_history("session use-selected")
    emit(ctx, {"selected": selected, "session": session_mod.build_session_payload(state)})
    return 0


@session.command("clear-library")
@click.pass_context
def session_clear_library(ctx: click.Context) -> int:
    state = current_session()
    state["current_library"] = None
    session_mod.save_session_state(state)
    session_mod.append_command_history("session clear-library")
    emit(ctx, session_mod.build_session_payload(state))
    return 0


@session.command("clear-collection")
@click.pass_context
def session_clear_collection(ctx: click.Context) -> int:
    state = current_session()
    state["current_collection"] = None
    session_mod.save_session_state(state)
    session_mod.append_command_history("session clear-collection")
    emit(ctx, session_mod.build_session_payload(state))
    return 0


@session.command("clear-item")
@click.pass_context
def session_clear_item(ctx: click.Context) -> int:
    state = current_session()
    state["current_item"] = None
    session_mod.save_session_state(state)
    session_mod.append_command_history("session clear-item")
    emit(ctx, session_mod.build_session_payload(state))
    return 0


@session.command("history")
@click.option("--limit", default=10, show_default=True, type=int)
@click.pass_context
def session_history(ctx: click.Context, limit: int) -> int:
    emit(ctx, {"history": current_session().get("command_history", [])[-limit:]})
    return 0


def repl_help_text() -> str:
    return """Interactive REPL for zotero-cli

Builtins:
  help                    Show this help
  exit, quit              Leave the REPL
  current-library         Show the current library reference
  current-collection      Show the current collection reference
  current-item            Show the current item reference
  use-library <ref>       Persist current library
  use-collection <ref>    Persist current collection
  use-item <ref>          Persist current item
  use-selected            Read and persist the collection selected in Zotero
  clear-library           Clear current library
  clear-collection        Clear current collection
  clear-item              Clear current item
  status                  Show current session status
  history [limit]         Show recent command history
  state-path              Show the session state file path
"""


def _repl_echo(config: RootCliConfig, data: Any = None, *, text: str | None = None) -> None:
    if config.json_output:
        click.echo(_json_text(data))
        return
    if text is not None:
        click.echo(_safe_text_for_stdout(text))
        return
    if isinstance(data, str):
        click.echo(_safe_text_for_stdout(data))
        return
    click.echo(_json_text(data))


def _handle_repl_builtin(argv: list[str], skin: ReplSkin, config: RootCliConfig) -> tuple[bool, int]:
    if not argv:
        return True, 0
    cmd = argv[0]
    state = current_session()
    if cmd in {"exit", "quit"}:
        return True, 1
    if cmd == "help":
        click.echo(repl_help_text())
        return True, 0
    if cmd == "current-library":
        _repl_echo(
            config,
            {"current_library": state.get("current_library")},
            text=f"Current library: {state.get('current_library') or '<unset>'}",
        )
        return True, 0
    if cmd == "current-collection":
        _repl_echo(
            config,
            {"current_collection": state.get("current_collection")},
            text=f"Current collection: {state.get('current_collection') or '<unset>'}",
        )
        return True, 0
    if cmd == "current-item":
        _repl_echo(
            config,
            {"current_item": state.get("current_item")},
            text=f"Current item: {state.get('current_item') or '<unset>'}",
        )
        return True, 0
    if cmd == "status":
        _repl_echo(config, session_mod.build_session_payload(state))
        return True, 0
    if cmd == "history":
        limit = 10
        if len(argv) > 1:
            try:
                limit = max(1, int(argv[1]))
            except ValueError:
                skin.warning(f"history limit must be an integer: {argv[1]}")
                return True, 0
        _repl_echo(config, {"history": state.get("command_history", [])[-limit:]})
        return True, 0
    if cmd == "state-path":
        _repl_echo(config, {"state_path": str(session_mod.session_state_path())}, text=str(session_mod.session_state_path()))
        return True, 0
    if cmd == "use-library" and len(argv) > 1:
        library_ref = " ".join(argv[1:])
        try:
            state["current_library"] = _normalize_session_library(_build_runtime_from_config(config), library_ref)
        except click.ClickException as exc:
            skin.error(exc.format_message())
            return True, 0
        session_mod.save_session_state(state)
        session_mod.append_command_history(f"use-library {library_ref}")
        _repl_echo(
            config,
            session_mod.build_session_payload(state),
            text=f"Current library: {state['current_library']}",
        )
        return True, 0
    if cmd == "use-collection" and len(argv) > 1:
        state["current_collection"] = " ".join(argv[1:])
        session_mod.save_session_state(state)
        session_mod.append_command_history(f"use-collection {' '.join(argv[1:])}")
        _repl_echo(
            config,
            session_mod.build_session_payload(state),
            text=f"Current collection: {state['current_collection']}",
        )
        return True, 0
    if cmd == "use-item" and len(argv) > 1:
        state["current_item"] = " ".join(argv[1:])
        session_mod.save_session_state(state)
        session_mod.append_command_history(f"use-item {' '.join(argv[1:])}")
        _repl_echo(
            config,
            session_mod.build_session_payload(state),
            text=f"Current item: {state['current_item']}",
        )
        return True, 0
    if cmd == "clear-library":
        state["current_library"] = None
        session_mod.save_session_state(state)
        _repl_echo(config, session_mod.build_session_payload(state), text="Current library cleared.")
        return True, 0
    if cmd == "clear-collection":
        state["current_collection"] = None
        session_mod.save_session_state(state)
        _repl_echo(config, session_mod.build_session_payload(state), text="Current collection cleared.")
        return True, 0
    if cmd == "clear-item":
        state["current_item"] = None
        session_mod.save_session_state(state)
        _repl_echo(config, session_mod.build_session_payload(state), text="Current item cleared.")
        return True, 0
    if cmd == "use-selected":
        try:
            runtime = _build_runtime_from_config(config)
            selected = catalog.use_selected_collection(runtime)
        except Exception as exc:
            skin.error(str(exc))
            return True, 0
        persisted_state = _persist_selected_collection(selected)
        session_mod.append_command_history("use-selected")
        if config.json_output:
            _repl_echo(config, {"selected": selected, "session": session_mod.build_session_payload(persisted_state)})
        else:
            _repl_echo(config, selected)
        return True, 0
    return False, 0


def _supports_fancy_repl_output() -> bool:
    is_tty = getattr(sys.stdout, "isatty", lambda: False)()
    if not is_tty:
        return False
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "▸↑⊙﹞".encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _safe_print_banner(skin: ReplSkin) -> None:
    if not _supports_fancy_repl_output():
        click.echo("zotero-cli REPL")
        click.echo(f"Skill: {skin.skill_path}")
        click.echo("Type help for commands, quit to exit")
        return
    try:
        skin.print_banner()
    except UnicodeEncodeError:
        click.echo("zotero-cli REPL")
        click.echo(f"Skill: {skin.skill_path}")
        click.echo("Type help for commands, quit to exit")


def _safe_print_goodbye(skin: ReplSkin) -> None:
    if not _supports_fancy_repl_output():
        click.echo("Goodbye!")
        return
    try:
        skin.print_goodbye()
    except UnicodeEncodeError:
        click.echo("Goodbye!")


def run_repl(config: RootCliConfig | None = None) -> int:
    config = config or RootCliConfig()
    skin = ReplSkin("zotero", version=__version__)
    prompt_session = None
    try:
        prompt_session = skin.create_prompt_session()
    except NoConsoleScreenBufferError:
        prompt_session = None
    _safe_print_banner(skin)
    while True:
        try:
            if prompt_session is None:
                line = input("zotero> ").strip()
            else:
                line = skin.get_input(prompt_session).strip()
        except EOFError:
            click.echo()
            _safe_print_goodbye(skin)
            return 0
        except KeyboardInterrupt:
            click.echo()
            continue
        if not line:
            continue
        try:
            argv = shlex.split(line)
        except ValueError as exc:
            skin.error(f"parse error: {exc}")
            continue
        handled, control = _handle_repl_builtin(argv, skin, config)
        if handled:
            if control == 1:
                _safe_print_goodbye(skin)
                return 0
            continue
        expanded = session_mod.expand_repl_aliases_with_state(argv, current_session())
        result = dispatch(_repl_root_args(config) + expanded)
        if result not in (0, None):
            skin.warning(f"command exited with status {result}")
        else:
            session_mod.append_command_history(line)


@cli.command("repl")
@click.pass_context
def repl_command(ctx: click.Context) -> int:
    """Start the interactive REPL."""
    return run_repl(_current_cli_config(ctx))


@cli.group("mcp")
def mcp_group() -> None:
    """MCP (Model Context Protocol) server commands."""


@mcp_group.command("serve")
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default="stdio", show_default=True,
              help="Transport mode. Use 'stdio' for Claude Desktop/Cursor, 'sse' for HTTP clients.")
@click.pass_context
def mcp_serve(ctx: click.Context, transport: str) -> None:
    """Start the MCP server for Claude Desktop, Cursor, or other MCP clients."""
    _run_mcp_server(_current_cli_config(ctx), transport)


def _run_mcp_server(config: RootCliConfig, transport: str) -> None:
    try:
        from cli_anything.zotero.mcp_server import create_server
    except ImportError as exc:
        raise RuntimeError(str(exc)) from exc
    srv = create_server(
        backend=config.backend,
        data_dir=config.data_dir,
        profile_dir=config.profile_dir,
        executable=config.executable,
    )
    srv.run(transport=transport)


@click.command(context_settings=CONTEXT_SETTINGS)
@click.option("--backend", type=click.Choice(["auto", "sqlite", "api"]), default="auto", show_default=True)
@click.option("--data-dir", default=None, help="Explicit Zotero data directory.")
@click.option("--profile-dir", default=None, help="Explicit Zotero profile directory.")
@click.option("--executable", default=None, help="Explicit Zotero executable path.")
@click.option("--transport", type=click.Choice(["stdio", "sse"]), default="stdio", show_default=True,
              help="Transport mode. Use 'stdio' for Claude Desktop/Cursor, 'sse' for HTTP clients.")
def mcp_cli(backend: str, data_dir: str | None, profile_dir: str | None, executable: str | None, transport: str) -> None:
    """Start the Zotero MCP server."""
    _run_mcp_server(
        RootCliConfig(
            backend=backend,
            data_dir=data_dir,
            profile_dir=profile_dir,
            executable=executable,
            json_output=False,
        ),
        transport,
    )


def dispatch(argv: list[str] | None = None, prog_name: str | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in args
    try:
        result = cli.main(args=args, prog_name=prog_name or "zotero-cli", standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.ClickException as exc:
        if json_mode:
            click.echo(_json_text({"error": exc.format_message()}))
        else:
            exc.show()
        return int(exc.exit_code)
    except RuntimeError as exc:
        if json_mode:
            click.echo(_json_text({"error": str(exc)}))
        else:
            click.echo(f"Error: {exc}", err=True)
        return 1
    return int(result or 0)


def entrypoint(argv: list[str] | None = None) -> int:
    return dispatch(argv, prog_name=sys.argv[0])


def mcp_entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        result = mcp_cli.main(args=args, prog_name="zotero-mcp", standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.ClickException as exc:
        exc.show()
        return int(exc.exit_code)
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        return 1
    return int(result or 0)
