"""Runtime health checks for agent-facing use."""

from __future__ import annotations

from typing import Any

from cli_anything.zotero import __version__
from cli_anything.zotero.core.discovery import RuntimeContext
from cli_anything.zotero.core.jsbridge import JSBridgeClient
from cli_anything.zotero.utils import zotero_paths


def run_doctor(runtime: RuntimeContext, bridge: JSBridgeClient) -> dict[str, Any]:
    """Aggregate connector / local API / plugin / bridge health."""
    profile_dir = runtime.environment.profile_dir
    xpi_path = zotero_paths.plugin_xpi_path(profile_dir)
    installed = zotero_paths.plugin_installed(profile_dir)
    installed_version = zotero_paths.installed_plugin_version(profile_dir)
    bundled_version = zotero_paths.bundled_plugin_version()
    update_available = bool(
        installed_version and bundled_version and installed_version != bundled_version
    )
    active = bridge.bridge_endpoint_active()

    js_ok = False
    js_error = None
    js_result = None
    zotero_js_version = None
    if active:
        result = bridge.execute_js_http_required(
            "return {ok: true, value: 'cli-bridge-ok', version: Zotero.version};",
            wait_seconds=5,
        )
        data = result.get("data")
        js_ok = bool(
            result.get("ok")
            and isinstance(data, dict)
            and data.get("value") == "cli-bridge-ok"
        )
        js_result = data
        js_error = result.get("error")
        if isinstance(data, dict):
            zotero_js_version = data.get("version")

    checks = {
        "package": {
            "ok": True,
            "version": __version__,
        },
        "zotero_app": {
            "ok": bool(runtime.environment.version),
            "version": runtime.environment.version,
            "executable": str(runtime.environment.executable)
            if runtime.environment.executable
            else None,
            "data_dir": str(runtime.environment.data_dir)
            if runtime.environment.data_dir
            else None,
            "profile_dir": str(profile_dir) if profile_dir else None,
        },
        "connector": {
            "ok": bool(runtime.connector_available),
            "message": runtime.connector_message,
        },
        "local_api": {
            "ok": bool(runtime.local_api_available),
            "message": runtime.local_api_message,
            "configured": bool(runtime.environment.local_api_enabled_configured),
        },
        "plugin": {
            "ok": bool(installed and not update_available),
            "xpi_installed": installed,
            "xpi_path": str(xpi_path) if xpi_path else None,
            "installed_version": installed_version,
            "bundled_version": bundled_version,
            "update_available": update_available,
        },
        "bridge": {
            "ok": bool(active and js_ok),
            "endpoint_active": active,
            "js_ok": js_ok,
            "js_error": js_error,
            "js_result": js_result,
            "zotero_js_version": zotero_js_version,
        },
    }

    next_steps: list[str] = []
    if not checks["connector"]["ok"]:
        next_steps.append("Start Zotero desktop (connector is not available).")
    if not checks["local_api"]["ok"]:
        next_steps.append(
            "Enable Local API: zotero-cli app enable-local-api --launch "
            "(or Zotero Settings → Advanced → allow other apps)."
        )
    if not installed:
        next_steps.append("Install CLI Bridge: zotero-cli app install-plugin, then restart Zotero.")
    elif update_available:
        next_steps.append(
            f"Upgrade CLI Bridge {installed_version} → {bundled_version}: "
            "zotero-cli app install-plugin, then restart Zotero."
        )
    elif not active:
        next_steps.append("Restart Zotero so /cli-bridge/eval is registered.")
    elif not js_ok:
        next_steps.append(
            "Bridge endpoint is up but eval failed; reinstall plugin and restart Zotero."
        )

    ready = all(bool(c.get("ok")) for c in checks.values())
    write_ready = bool(
        checks["connector"]["ok"] and checks["plugin"]["ok"] and checks["bridge"]["ok"]
    )
    read_ready = bool(checks["zotero_app"]["ok"] and runtime.environment.sqlite_path)

    return {
        "action": "app_doctor",
        "ok": ready,
        "status": "ready" if ready else "degraded",
        "code": "READY" if ready else "DEGRADED",
        "ready": ready,
        "read_ready": read_ready,
        "write_ready": write_ready,
        "checks": checks,
        "next_steps": next_steps
        if next_steps
        else ["CLI Bridge and local surfaces look healthy."],
        "summary": (
            "All systems ready for agent read/write."
            if ready
            else "Some surfaces are unavailable; see checks and next_steps."
        ),
    }


def plugin_version_warning(runtime: RuntimeContext) -> dict[str, Any] | None:
    """Return a warning dict when installed bridge plugin is behind bundled."""
    profile_dir = runtime.environment.profile_dir
    installed = zotero_paths.installed_plugin_version(profile_dir)
    bundled = zotero_paths.bundled_plugin_version()
    if installed and bundled and installed != bundled:
        return {
            "warning": "plugin_version_mismatch",
            "installed_version": installed,
            "bundled_version": bundled,
            "message": (
                f"CLI Bridge plugin {installed} != bundled {bundled}. "
                "Run: zotero-cli app install-plugin, then restart Zotero."
            ),
        }
    if not zotero_paths.plugin_installed(profile_dir):
        return {
            "warning": "plugin_missing",
            "installed_version": None,
            "bundled_version": bundled,
            "message": "CLI Bridge plugin not installed. Run: zotero-cli app install-plugin",
        }
    return None
