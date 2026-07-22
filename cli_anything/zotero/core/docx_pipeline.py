"""One-shot DOCX citation pipeline for agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cli_anything.zotero.core import docx as docx_tools
from cli_anything.zotero.core import docx_static, docx_zoterify
from cli_anything.zotero.core.discovery import RuntimeContext
from cli_anything.zotero.core.results import result_payload


def cite_document(
    runtime: RuntimeContext,
    bridge: Any,
    path: str | Path,
    output: str | Path,
    *,
    mode: str = "auto",
    style: str = docx_static.DEFAULT_STYLE,
    locale: str = docx_static.DEFAULT_LOCALE,
    bibliography: str = "auto",
    session: dict[str, Any] | None = None,
    force: bool = False,
    open_document: bool = True,
    debug_dir: str | Path | None = None,
    backend: str = docx_zoterify.DEFAULT_BACKEND,
) -> dict[str, Any]:
    """Validate placeholders and convert to static or dynamic citations.

    mode:
      - static: always render plain-text citations
      - dynamic: Zotero/LibreOffice fields (requires environment)
      - auto: dynamic if installation_ready, else static
    """
    mode = (mode or "auto").lower().strip()
    if mode not in {"auto", "static", "dynamic"}:
        return result_payload(
            action="docx_cite",
            ok=False,
            status="error",
            code="INVALID_MODE",
            error="mode must be auto|static|dynamic",
        )

    source = Path(path).expanduser()
    out = Path(output).expanduser()

    # Step 1: validate placeholders
    try:
        validation = docx_tools.validate_placeholders(runtime, source, session=session)
    except Exception as exc:
        return result_payload(
            action="docx_cite",
            ok=False,
            status="error",
            code="VALIDATE_FAILED",
            error=str(exc),
            path=str(source),
        )

    steps: list[dict[str, Any]] = [
        {
            "step": "validate_placeholders",
            "ok": bool(validation.get("ok")),
            "placeholder_count": validation.get("placeholder_count"),
            "valid_count": validation.get("valid_count"),
            "missing_keys": validation.get("missing_keys") or [],
            "invalid_placeholders": validation.get("invalid_placeholders") or [],
        }
    ]

    if not validation.get("ok"):
        return result_payload(
            action="docx_cite",
            ok=False,
            status="error",
            code="PLACEHOLDERS_INVALID",
            path=str(source),
            error="DOCX placeholders are invalid or missing from the local library",
            validation=validation,
            steps=steps,
            next_steps=[
                "Fix missing keys or invalid {{zotero:KEY}} placeholders",
                "Run: zotero-cli --json docx validate-placeholders <file>",
            ],
        )
    if not validation.get("placeholder_count"):
        return result_payload(
            action="docx_cite",
            ok=False,
            status="error",
            code="NO_PLACEHOLDERS",
            path=str(source),
            error="No {{zotero:ITEMKEY}} placeholders found",
            validation=validation,
            steps=steps,
        )

    # Step 2: environment for dynamic mode
    doctor = None
    chosen = mode
    if mode in {"auto", "dynamic"}:
        try:
            doctor = docx_zoterify.zoterify_doctor(runtime, bridge, backend=backend)
        except Exception as exc:
            doctor = {"ok": False, "installation_ready": False, "error": str(exc)}
        steps.append(
            {
                "step": "docx_doctor",
                "ok": bool(doctor.get("installation_ready") or doctor.get("ok")),
                "installation_ready": doctor.get("installation_ready"),
                "next_steps": doctor.get("next_steps"),
            }
        )
        if mode == "auto":
            chosen = "dynamic" if doctor.get("installation_ready") else "static"
        elif mode == "dynamic" and not doctor.get("installation_ready"):
            return result_payload(
                action="docx_cite",
                ok=False,
                status="error",
                code="DYNAMIC_NOT_READY",
                path=str(source),
                error="Dynamic DOCX citations are not ready (LibreOffice/Zotero plugin)",
                doctor=doctor,
                steps=steps,
                next_steps=doctor.get("next_steps")
                or [
                    "zotero-cli --json docx doctor",
                    "Install LibreOffice + Zotero LibreOffice plugin + CLI Bridge",
                    "Or use --mode static",
                ],
            )

    # Step 3: convert
    try:
        if chosen == "static":
            convert = docx_static.render_static_citations(
                runtime,
                source,
                out,
                style=style,
                locale=locale,
                bibliography=bibliography,
                session=session,
                overwrite=force,
            )
        else:
            convert = docx_zoterify.zoterify_document(
                runtime,
                bridge,
                source,
                out,
                backend=backend,
                style=style,
                locale=locale,
                bibliography=bibliography,
                session=session,
                open_document=open_document,
                overwrite=force,
                debug_dir=debug_dir,
            )
    except Exception as exc:
        steps.append({"step": f"convert_{chosen}", "ok": False, "error": str(exc)})
        # auto fallback: if dynamic failed, try static once
        if chosen == "dynamic" and mode == "auto":
            try:
                convert = docx_static.render_static_citations(
                    runtime,
                    source,
                    out,
                    style=style,
                    locale=locale,
                    bibliography=bibliography,
                    session=session,
                    overwrite=force,
                )
                chosen = "static"
                steps.append(
                    {
                        "step": "convert_static_fallback",
                        "ok": True,
                        "note": f"dynamic failed: {exc}",
                    }
                )
            except Exception as exc2:
                steps.append({"step": "convert_static_fallback", "ok": False, "error": str(exc2)})
                return result_payload(
                    action="docx_cite",
                    ok=False,
                    status="error",
                    code="CONVERT_FAILED",
                    path=str(source),
                    error=f"dynamic failed ({exc}); static fallback failed ({exc2})",
                    doctor=doctor,
                    steps=steps,
                )
        else:
            return result_payload(
                action="docx_cite",
                ok=False,
                status="error",
                code="CONVERT_FAILED",
                path=str(source),
                mode_requested=mode,
                mode_used=chosen,
                error=str(exc),
                doctor=doctor,
                steps=steps,
            )

    steps.append({"step": f"convert_{chosen}", "ok": True, "output": str(out)})
    return result_payload(
        action="docx_cite",
        ok=True,
        status="success",
        code="CITED",
        path=str(source),
        output=str(out),
        mode_requested=mode,
        mode_used=chosen,
        style=style,
        locale=locale,
        bibliography=bibliography,
        placeholder_count=validation.get("placeholder_count"),
        validation_summary={
            "valid_count": validation.get("valid_count"),
            "unique_keys": validation.get("unique_keys"),
        },
        convert=convert,
        doctor=doctor,
        steps=steps,
    )
