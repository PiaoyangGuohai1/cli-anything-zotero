from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from cli_anything.zotero.utils import zotero_http, zotero_paths


@dataclass
class RuntimeContext:
    environment: zotero_paths.ZoteroEnvironment
    backend: str
    connector_available: bool
    connector_message: str
    local_api_available: bool
    local_api_message: str

    def to_status_payload(self) -> dict[str, Any]:
        payload = self.environment.to_dict()
        payload.update(
            {
                "backend": self.backend,
                "connector_available": self.connector_available,
                "connector_message": self.connector_message,
                "local_api_available": self.local_api_available,
                "local_api_message": self.local_api_message,
            }
        )
        return payload


def build_runtime_context(*, backend: str = "auto", data_dir: str | None = None, profile_dir: str | None = None, executable: str | None = None) -> RuntimeContext:
    environment = zotero_paths.build_environment(
        explicit_data_dir=data_dir,
        explicit_profile_dir=profile_dir,
        explicit_executable=executable,
    )
    connector_available, connector_message = zotero_http.connector_is_available(environment.port)
    local_api_available, local_api_message = zotero_http.local_api_is_available(environment.port)
    return RuntimeContext(
        environment=environment,
        backend=backend,
        connector_available=connector_available,
        connector_message=connector_message,
        local_api_available=local_api_available,
        local_api_message=local_api_message,
    )


def _macos_app_bundle_for_executable(executable: Path) -> Path | None:
    for parent in (executable, *executable.parents):
        if parent.suffix == ".app":
            return parent
    return None


def launch_zotero(runtime: RuntimeContext, wait_timeout: int = 30) -> dict[str, Any]:
    executable = runtime.environment.executable
    if executable is None:
        raise RuntimeError("Zotero executable could not be resolved")
    if not executable.exists():
        raise FileNotFoundError(f"Zotero executable not found: {executable}")

    launch_command = [str(executable)]
    if sys.platform == "darwin":
        app_bundle = _macos_app_bundle_for_executable(executable)
        if app_bundle is not None and app_bundle.exists():
            launch_command = ["open", str(app_bundle)]
    process = subprocess.Popen(launch_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    connector_ready = zotero_http.wait_for_endpoint(
        runtime.environment.port,
        "/connector/ping",
        timeout=wait_timeout,
        ready_statuses=(200,),
    )
    local_api_ready = False
    if runtime.environment.local_api_enabled_configured:
        local_api_ready = zotero_http.wait_for_endpoint(
            runtime.environment.port,
            "/api/",
            timeout=wait_timeout,
            headers={"Zotero-API-Version": zotero_http.LOCAL_API_VERSION},
            ready_statuses=(200,),
        )
    return {
        "action": "launch",
        "pid": process.pid,
        "connector_ready": connector_ready,
        "local_api_ready": local_api_ready,
        "wait_timeout": wait_timeout,
        "executable": str(executable),
    }


def ensure_local_api_ready(runtime: RuntimeContext, *, wait_timeout: int = 30) -> dict[str, Any]:
    """Launch Zotero when needed and wait for the Local API used by CSL rendering."""
    if runtime.local_api_available:
        return {"attempted": False, "ok": True, "local_api_ready": True, "reason": "local API already available"}
    try:
        launch = launch_zotero(runtime, wait_timeout=wait_timeout)
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        return {
            "attempted": True,
            "ok": False,
            "local_api_ready": False,
            "launch": None,
            "error": str(exc),
        }
    runtime.connector_available = bool(launch.get("connector_ready"))
    runtime.connector_message = "connector available" if runtime.connector_available else "connector unavailable after launch"
    runtime.local_api_available = bool(launch.get("local_api_ready"))
    runtime.local_api_message = "local API available" if runtime.local_api_available else "local API unavailable after launch"
    return {
        "attempted": True,
        "ok": runtime.local_api_available,
        "local_api_ready": runtime.local_api_available,
        "launch": launch,
        "error": None if runtime.local_api_available else "Zotero Local API was not available after launching Zotero.",
    }


def ensure_bridge_endpoint_ready(
    runtime: RuntimeContext,
    bridge: Any,
    *,
    wait_timeout: int = 30,
    poll_interval: float = 0.5,
) -> dict[str, Any]:
    """Launch Zotero when needed and wait for the CLI Bridge endpoint."""
    if bridge.bridge_endpoint_active():
        return {"attempted": False, "ok": True, "endpoint_active": True, "reason": "CLI Bridge endpoint already active"}
    try:
        launch = launch_zotero(runtime, wait_timeout=wait_timeout)
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        return {
            "attempted": True,
            "ok": False,
            "endpoint_active": False,
            "launch": None,
            "error": str(exc),
        }
    runtime.connector_available = bool(launch.get("connector_ready"))
    runtime.connector_message = "connector available" if runtime.connector_available else "connector unavailable after launch"
    deadline = time.time() + wait_timeout
    endpoint_active = False
    while time.time() < deadline:
        if bridge.bridge_endpoint_active():
            endpoint_active = True
            break
        time.sleep(poll_interval)
    return {
        "attempted": True,
        "ok": endpoint_active,
        "endpoint_active": endpoint_active,
        "launch": launch,
        "error": None if endpoint_active else "CLI Bridge endpoint was not active after launching Zotero.",
    }


def ensure_live_api_enabled(profile_dir: Optional[str] = None) -> Optional[str]:
    environment = zotero_paths.build_environment(explicit_profile_dir=profile_dir)
    path = zotero_paths.ensure_local_api_enabled(environment.profile_dir)
    return str(path) if path else None
