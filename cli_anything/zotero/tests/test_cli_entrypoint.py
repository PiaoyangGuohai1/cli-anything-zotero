from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cli_anything.zotero.tests._helpers import create_sample_environment, fake_zotero_http_server, sample_pdf_bytes
from cli_anything.zotero.core import session as session_mod
from cli_anything.zotero.zotero_cli import RootCliConfig, _handle_repl_builtin, dispatch, mcp_entrypoint, repl_help_text, run_repl
from cli_anything.zotero.tests.test_core import write_docx_with_document_xml


REPO_ROOT = Path(__file__).resolve().parents[4]


def resolve_cli() -> list[str]:
    force_installed = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    installed = shutil.which("zotero-cli") or shutil.which("cli-anything-zotero")
    if installed:
        return [installed]
    scripts_dir = Path(sysconfig.get_path("scripts"))
    for candidate in (
        scripts_dir / "zotero-cli.exe",
        scripts_dir / "zotero-cli",
        scripts_dir / "cli-anything-zotero.exe",
        scripts_dir / "cli-anything-zotero",
    ):
        if candidate.exists():
            return [str(candidate)]
    if force_installed:
        raise RuntimeError("zotero-cli not found in PATH. Install it with: py -m pip install -e .")
    return [sys.executable, "-m", "cli_anything.zotero"]


def uses_module_fallback(cli_base: list[str]) -> bool:
    return len(cli_base) >= 3 and cli_base[1] == "-m"


class CliEntrypointTests(unittest.TestCase):
    CLI_BASE = resolve_cli()

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.env_paths = create_sample_environment(Path(self.tmpdir.name))

    def run_cli(self, args, input_text=None, extra_env=None):
        env = os.environ.copy()
        if uses_module_fallback(self.CLI_BASE):
            env["PYTHONPATH"] = str(REPO_ROOT / "zotero" / "agent-harness") + os.pathsep + env.get("PYTHONPATH", "")
        env["ZOTERO_PROFILE_DIR"] = str(self.env_paths["profile_dir"])
        env["ZOTERO_DATA_DIR"] = str(self.env_paths["data_dir"])
        env["ZOTERO_EXECUTABLE"] = str(self.env_paths["executable"])
        env["ZOTERO_HTTP_PORT"] = "23191"
        env["CLI_ANYTHING_ZOTERO_STATE_DIR"] = str(Path(self.tmpdir.name) / "state")
        if extra_env:
            env.update(extra_env)
        return subprocess.run(self.CLI_BASE + args, input=input_text, capture_output=True, text=True, env=env)

    def test_help_renders_groups(self):
        result = self.run_cli(["--help"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("collection", result.stdout)
        self.assertIn("docx", result.stdout)
        self.assertIn("item", result.stdout)
        self.assertIn("import", result.stdout)
        self.assertIn("note", result.stdout)
        self.assertIn("session", result.stdout)

    def test_version_option_reports_package_version(self):
        result = self.run_cli(["--version"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("0.9.0", result.stdout)

    def test_dispatch_uses_requested_prog_name(self):
        result = dispatch(["--help"], prog_name="zotero-cli")
        self.assertEqual(result, 0)

    def test_zotero_mcp_entrypoint_runs_server_directly(self):
        fake_server = mock.Mock()
        fake_module = types.SimpleNamespace(create_server=mock.Mock(return_value=fake_server))

        with mock.patch.dict("sys.modules", {"cli_anything.zotero.mcp_server": fake_module}):
            result = mcp_entrypoint([
                "--backend",
                "sqlite",
                "--data-dir",
                "D:/zotero-data",
                "--profile-dir",
                "D:/zotero-profile",
                "--executable",
                "D:/Program Files/Zotero/zotero.exe",
                "--transport",
                "sse",
            ])

        self.assertEqual(result, 0)
        fake_module.create_server.assert_called_once_with(
            backend="sqlite",
            data_dir="D:/zotero-data",
            profile_dir="D:/zotero-profile",
            executable="D:/Program Files/Zotero/zotero.exe",
        )
        fake_server.run.assert_called_once_with(transport="sse")

    def test_zotero_mcp_entrypoint_reports_missing_mcp_dependency_cleanly(self):
        with mock.patch.dict("sys.modules", {"cli_anything.zotero.mcp_server": None}):
            with mock.patch("click.echo") as echo:
                result = mcp_entrypoint([])

        self.assertEqual(result, 1)
        self.assertIn("Error:", echo.call_args.args[0])

    def test_force_installed_mode_requires_real_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict("os.environ", {"CLI_ANYTHING_FORCE_INSTALLED": "1"}, clear=False):
                with mock.patch("shutil.which", return_value=None):
                    with mock.patch("sysconfig.get_path", return_value=tmpdir):
                        with self.assertRaises(RuntimeError):
                            resolve_cli()

    def test_repl_help_text_mentions_builtins(self):
        self.assertIn("use-selected", repl_help_text())
        self.assertIn("current-item", repl_help_text())

    def test_default_entrypoint_starts_repl(self):
        result = self.run_cli([], input_text="exit\n")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("zotero-cli", result.stdout)

    def test_docx_inspect_citations_reports_field_systems(self):
        docx_path = Path(self.tmpdir.name) / "citations.docx"
        write_docx_with_document_xml(
            docx_path,
            """
            <w:p>
              <w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE </w:instrText></w:r>
              <w:r><w:t>[1]</w:t></w:r>
            </w:p>
            """,
        )

        result = self.run_cli(["--json", "docx", "inspect-citations", str(docx_path)])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["field_counts"]["endnote"], 1)
        self.assertIn("static-text", payload["systems"])

    def test_docx_inspect_placeholders_reports_zotero_keys(self):
        docx_path = Path(self.tmpdir.name) / "placeholders.docx"
        write_docx_with_document_xml(
            docx_path,
            '<w:p><w:r><w:t>Claim {{zotero:REG12345, GROUPKEY}}.</w:t></w:r></w:p>',
        )

        result = self.run_cli(["--json", "docx", "inspect-placeholders", str(docx_path)])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["citation_count"], 2)
        self.assertEqual(payload["unique_keys"], ["GROUPKEY", "REG12345"])

    def test_docx_validate_placeholders_reports_missing_keys(self):
        docx_path = Path(self.tmpdir.name) / "validate.docx"
        write_docx_with_document_xml(
            docx_path,
            '<w:p><w:r><w:t>Known {{zotero:REG12345}} and missing {{zotero:NOITEM99}}.</w:t></w:r></w:p>',
        )

        result = self.run_cli(["--json", "docx", "validate-placeholders", str(docx_path)])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["valid_count"], 1)
        self.assertEqual(payload["missing_keys"], ["NOITEM99"])

    def test_docx_zoterify_preflight_can_skip_external_checks(self):
        docx_path = Path(self.tmpdir.name) / "ready.docx"
        write_docx_with_document_xml(
            docx_path,
            '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
        )

        result = self.run_cli(["--json", "docx", "zoterify-preflight", "--skip-external-checks", str(docx_path)])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["checks"]["external"]["skipped"])
        self.assertEqual(payload["checks"]["placeholders"]["citation_count"], 1)

    def test_docx_prepare_zotero_import_writes_output_file(self):
        docx_path = Path(self.tmpdir.name) / "ready.docx"
        output_path = Path(self.tmpdir.name) / "transfer.docx"
        write_docx_with_document_xml(
            docx_path,
            '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
        )

        result = self.run_cli(
            [
                "--json",
                "docx",
                "prepare-zotero-import",
                "--experimental",
                "--skip-external-checks",
                "--output",
                str(output_path),
                str(docx_path),
            ]
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["output"], str(output_path))
        self.assertEqual(payload["citation_count"], 1)
        self.assertTrue(output_path.exists())

    def test_docx_zoterify_probe_reports_inactive_bridge(self):
        result = self.run_cli(["--json", "docx", "zoterify-probe"])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ready"])
        self.assertIn("bridge", payload)
        self.assertFalse(payload["bridge"]["active"])
        self.assertIn("install-plugin", payload["bridge"]["next_step"])

    def test_docx_doctor_reports_optional_requirements(self):
        result = self.run_cli(["--json", "docx", "doctor"])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["workflow"], "optional LibreOffice-backed dynamic DOCX citations")
        self.assertIn("requirements", payload)
        self.assertIn("cli_bridge_plugin", payload["requirements"])
        self.assertIn("libreoffice", payload["requirements"])
        self.assertIn("zotero_libreoffice_integration", payload["requirements"])
        self.assertIn("upgrade_steps", payload)

    def test_docx_zoterify_fails_when_bridge_is_inactive(self):
        docx_path = Path(self.tmpdir.name) / "ready.docx"
        output_path = Path(self.tmpdir.name) / "zotero.docx"
        write_docx_with_document_xml(
            docx_path,
            '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
        )

        result = self.run_cli(
            [
                "--json",
                "docx",
                "zoterify",
                "--output",
                str(output_path),
                "--no-open",
                str(docx_path),
            ]
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLI Bridge endpoint is not active", result.stdout)

    def test_docx_insert_citations_alias_exists_for_ai_workflows(self):
        result = self.run_cli(["docx", "insert-citations", "--help"])

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("--bibliography", result.stdout)
        self.assertIn("--debug-dir", result.stdout)

        doctor = self.run_cli(["docx", "doctor", "--help"])
        self.assertEqual(doctor.returncode, 0, msg=doctor.stderr)
        self.assertIn("dynamic DOCX citation requirements", doctor.stdout)

        render = self.run_cli(["docx", "render-citations", "--help"])
        self.assertEqual(render.returncode, 0, msg=render.stderr)
        self.assertIn("static citation", render.stdout)

    def test_docx_render_citations_writes_static_docx(self):
        docx_path = Path(self.tmpdir.name) / "ready.docx"
        output_path = Path(self.tmpdir.name) / "static.docx"
        write_docx_with_document_xml(
            docx_path,
            '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
        )
        with fake_zotero_http_server(sqlite_path=self.env_paths["sqlite_path"], data_dir=self.env_paths["data_dir"]) as server:
            result = self.run_cli(
                [
                    "--json",
                    "docx",
                    "render-citations",
                    "--output",
                    str(output_path),
                    str(docx_path),
                ],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "static")
        self.assertEqual(payload["bibliography_count"], 1)
        self.assertTrue(output_path.exists())
        document_xml = zipfile.ZipFile(output_path).read("word/document.xml").decode("utf-8")
        self.assertIn("REG12345 citation", document_xml)

    def test_export_bib_items_writes_output_file(self):
        output_path = Path(self.tmpdir.name) / "refs.bib"
        with fake_zotero_http_server(sqlite_path=self.env_paths["sqlite_path"], data_dir=self.env_paths["data_dir"]) as server:
            result = self.run_cli(
                [
                    "--json",
                    "--backend",
                    "api",
                    "export",
                    "bib",
                    "--items",
                    "REG12345",
                    "--output",
                    str(output_path),
                ],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["item_count"], 1)
        self.assertEqual(payload["output"], str(output_path))
        self.assertIn("@article{reg12345}", output_path.read_text(encoding="utf-8"))

    def test_export_bib_collection_writes_output_file(self):
        output_path = Path(self.tmpdir.name) / "collection.bib"
        with fake_zotero_http_server(sqlite_path=self.env_paths["sqlite_path"], data_dir=self.env_paths["data_dir"]) as server:
            result = self.run_cli(
                [
                    "--json",
                    "--backend",
                    "api",
                    "export",
                    "bib",
                    "--collection",
                    "GCOLLAAA",
                    "--output",
                    str(output_path),
                ],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["item_count"], 1)
        self.assertIn("@article{groupkey}", output_path.read_text(encoding="utf-8"))

    def test_repl_builtin_use_library_uses_root_runtime_config(self):
        config = RootCliConfig(
            backend="api",
            data_dir="D:/zotero-data",
            profile_dir="D:/zotero-profile",
            executable="D:/Program Files/Zotero/zotero.exe",
            json_output=True,
        )
        skin = mock.Mock()
        state = {"current_library": None, "current_collection": None, "current_item": None, "command_history": []}

        with mock.patch("cli_anything.zotero.zotero_cli.current_session", return_value=state):
            with mock.patch("cli_anything.zotero.zotero_cli.session_mod.save_session_state"):
                with mock.patch("cli_anything.zotero.zotero_cli.session_mod.append_command_history"):
                    with mock.patch("cli_anything.zotero.zotero_cli.discovery.build_runtime_context", return_value=object()) as build_runtime:
                        with mock.patch("cli_anything.zotero.zotero_cli._normalize_session_library", return_value=2):
                            with mock.patch("click.echo") as echo:
                                handled, control = _handle_repl_builtin(["use-library", "L2"], skin, config)

        self.assertTrue(handled)
        self.assertEqual(control, 0)
        build_runtime.assert_called_once_with(
            backend="api",
            data_dir="D:/zotero-data",
            profile_dir="D:/zotero-profile",
            executable="D:/Program Files/Zotero/zotero.exe",
        )
        emitted = json.loads(echo.call_args.args[0])
        self.assertEqual(emitted["current_library"], 2)

    def test_repl_builtin_use_selected_uses_root_runtime_config(self):
        config = RootCliConfig(
            backend="sqlite",
            data_dir="D:/zotero-data",
            profile_dir="D:/zotero-profile",
            executable="D:/Program Files/Zotero/zotero.exe",
            json_output=False,
        )
        runtime = object()
        selected = {"collectionID": 1, "collectionName": "Selected"}
        state = {"current_library": 1, "current_collection": "COLLAAAA", "current_item": None, "command_history": []}

        with mock.patch("cli_anything.zotero.zotero_cli.current_session", return_value=state):
            with mock.patch("cli_anything.zotero.zotero_cli.discovery.build_runtime_context", return_value=runtime) as build_runtime:
                with mock.patch("cli_anything.zotero.zotero_cli.catalog.use_selected_collection", return_value=selected) as use_selected:
                    with mock.patch("cli_anything.zotero.zotero_cli._persist_selected_collection", return_value=state):
                        with mock.patch("cli_anything.zotero.zotero_cli.session_mod.append_command_history"):
                            with mock.patch("click.echo"):
                                handled, control = _handle_repl_builtin(["use-selected"], mock.Mock(), config)

        self.assertTrue(handled)
        self.assertEqual(control, 0)
        build_runtime.assert_called_once_with(
            backend="sqlite",
            data_dir="D:/zotero-data",
            profile_dir="D:/zotero-profile",
            executable="D:/Program Files/Zotero/zotero.exe",
        )
        use_selected.assert_called_once_with(runtime)

    def test_json_repl_builtin_status_emits_structured_json(self):
        config = RootCliConfig(json_output=True)
        state = {"current_library": 1, "current_collection": "COLLAAAA", "current_item": "REG12345", "command_history": []}
        with mock.patch("cli_anything.zotero.zotero_cli.current_session", return_value=state):
            with mock.patch("click.echo") as echo:
                handled, control = _handle_repl_builtin(["status"], mock.Mock(), config)

        self.assertTrue(handled)
        self.assertEqual(control, 0)
        payload = json.loads(echo.call_args.args[0])
        self.assertEqual(payload["current_library"], 1)
        self.assertEqual(payload["current_item"], "REG12345")

    def test_run_repl_dispatches_commands_with_root_flags(self):
        config = RootCliConfig(
            backend="api",
            data_dir="D:/zotero-data",
            profile_dir="D:/zotero-profile",
            executable="D:/Program Files/Zotero/zotero.exe",
            json_output=True,
        )
        with mock.patch("cli_anything.zotero.zotero_cli.ReplSkin.create_prompt_session", return_value=None):
            with mock.patch("cli_anything.zotero.zotero_cli._safe_print_banner"), mock.patch(
                "cli_anything.zotero.zotero_cli._safe_print_goodbye"
            ):
                with mock.patch("builtins.input", side_effect=["item get REG12345", "exit"]):
                    with mock.patch("cli_anything.zotero.zotero_cli.current_session", return_value=session_mod.default_session_state()):
                        with mock.patch(
                            "cli_anything.zotero.zotero_cli.session_mod.expand_repl_aliases_with_state",
                            return_value=["item", "get", "REG12345"],
                        ):
                            with mock.patch("cli_anything.zotero.zotero_cli.dispatch", return_value=0) as dispatch_mock:
                                result = run_repl(config)

        self.assertEqual(result, 0)
        dispatch_mock.assert_called_once_with(
            [
                "--backend",
                "api",
                "--json",
                "--data-dir",
                "D:/zotero-data",
                "--profile-dir",
                "D:/zotero-profile",
                "--executable",
                "D:/Program Files/Zotero/zotero.exe",
                "item",
                "get",
                "REG12345",
            ]
        )

    def test_app_status_json(self):
        result = self.run_cli(["--json", "app", "status"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"sqlite_exists": true', result.stdout)

    def test_app_enable_local_api_json(self):
        result = self.run_cli(["--json", "app", "enable-local-api"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"enabled": true', result.stdout)
        self.assertIn('"already_enabled": false', result.stdout)

    def test_collection_list_json(self):
        result = self.run_cli(["--json", "collection", "list"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Sample Collection", result.stdout)

    def test_collection_find_json(self):
        result = self.run_cli(["--json", "collection", "find", "sample"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("COLLAAAA", result.stdout)

    def test_item_get_json(self):
        result = self.run_cli(["--json", "item", "get", "REG12345"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Sample Title", result.stdout)

    def test_item_find_and_notes_json(self):
        with fake_zotero_http_server() as server:
            result = self.run_cli(
                ["--json", "item", "find", "Sample", "--collection", "COLLAAAA"],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("REG12345", result.stdout)

        notes_result = self.run_cli(["--json", "item", "notes", "REG12345"])
        self.assertEqual(notes_result.returncode, 0, msg=notes_result.stderr)
        self.assertIn("Example note", notes_result.stdout)

    def test_note_get_and_add(self):
        result = self.run_cli(["--json", "note", "get", "NOTEKEY"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Example note", result.stdout)

        with fake_zotero_http_server() as server:
            add_result = self.run_cli(
                ["--json", "note", "add", "REG12345", "--text", "A new note", "--format", "text"],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
        self.assertEqual(add_result.returncode, 0, msg=add_result.stderr)
        self.assertIn('"action": "note_add"', add_result.stdout)

    def test_item_context_and_analyze(self):
        result = self.run_cli(["--json", "item", "context", "REG12345", "--include-notes", "--include-links"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"prompt_context"', result.stdout)
        self.assertIn('"doi_url"', result.stdout)

        with fake_zotero_http_server() as server:
            analyze_result = self.run_cli(
                ["--json", "item", "analyze", "REG12345", "--question", "Summarize", "--model", "gpt-test"],
                extra_env={
                    "OPENAI_API_KEY": "test-key",
                    "CLI_ANYTHING_ZOTERO_OPENAI_URL": f"http://127.0.0.1:{server['port']}/v1/responses",
                },
            )
        self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr)
        self.assertIn('"answer": "Analysis text"', analyze_result.stdout)

    def test_session_status_json(self):
        self.run_cli(["session", "use-item", "REG12345"])
        result = self.run_cli(["--json", "session", "status"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"current_item": "REG12345"', result.stdout)

    def test_session_use_library_normalizes_tree_view_library_ref(self):
        result = self.run_cli(["--json", "session", "use-library", "L2"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"current_library": 2', result.stdout)

    def test_group_library_routes_use_group_scope(self):
        with fake_zotero_http_server() as server:
            extra_env = {"ZOTERO_HTTP_PORT": str(server["port"])}
            use_library = self.run_cli(["--json", "session", "use-library", "L2"], extra_env=extra_env)
            self.assertEqual(use_library.returncode, 0, msg=use_library.stderr)

            find_result = self.run_cli(
                ["--json", "item", "find", "Group", "--collection", "GCOLLAAA"],
                extra_env=extra_env,
            )
            self.assertEqual(find_result.returncode, 0, msg=find_result.stderr)
            self.assertIn("GROUPKEY", find_result.stdout)

            export_result = self.run_cli(["--json", "item", "export", "GROUPKEY", "--format", "ris"], extra_env=extra_env)
            self.assertEqual(export_result.returncode, 0, msg=export_result.stderr)
            self.assertIn("GROUPKEY", export_result.stdout)

            citation_result = self.run_cli(
                ["--json", "item", "citation", "GROUPKEY", "--style", "apa", "--locale", "en-US"],
                extra_env=extra_env,
            )
            self.assertEqual(citation_result.returncode, 0, msg=citation_result.stderr)
            self.assertIn("citation", citation_result.stdout)

            bibliography_result = self.run_cli(
                ["--json", "item", "bibliography", "GROUPKEY", "--style", "apa", "--locale", "en-US"],
                extra_env=extra_env,
            )
            self.assertEqual(bibliography_result.returncode, 0, msg=bibliography_result.stderr)
            self.assertIn("bibliography", bibliography_result.stdout)

            search_result = self.run_cli(["--json", "search", "items", "GSEARCHKEY"], extra_env=extra_env)
            self.assertEqual(search_result.returncode, 0, msg=search_result.stderr)
            self.assertIn("GROUPKEY", search_result.stdout)

        get_paths = [entry["path"] for entry in server["calls"] if entry["method"] == "GET"]
        self.assertTrue(any("/api/groups/2/collections/GCOLLAAA/items/top" in path for path in get_paths))
        self.assertTrue(any("/api/groups/2/items/GROUPKEY?format=ris" in path for path in get_paths))
        self.assertTrue(any("/api/groups/2/items/GROUPKEY?format=json&include=citation" in path for path in get_paths))
        self.assertTrue(any("/api/groups/2/items/GROUPKEY?format=json&include=bib" in path for path in get_paths))
        self.assertTrue(any("/api/groups/2/searches/GSEARCHKEY/items?format=json" in path for path in get_paths))

    def test_import_file_subprocess(self):
        import_path = Path(self.tmpdir.name) / "sample.ris"
        import_path.write_text("TY  - JOUR\nTI  - Imported Sample\nER  - \n", encoding="utf-8")
        with fake_zotero_http_server() as server:
            result = self.run_cli(
                ["--json", "import", "file", str(import_path), "--collection", "COLLAAAA", "--tag", "alpha"],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"action": "import_file"', result.stdout)
        self.assertIn('"treeViewID": "C1"', result.stdout)

    def test_import_json_subprocess(self):
        import_path = Path(self.tmpdir.name) / "items.json"
        import_path.write_text('[{"itemType": "journalArticle", "title": "Imported JSON"}]', encoding="utf-8")
        with fake_zotero_http_server() as server:
            result = self.run_cli(
                ["--json", "import", "json", str(import_path), "--collection", "COLLAAAA", "--tag", "beta"],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"action": "import_json"', result.stdout)
        self.assertIn('"submitted_count": 1', result.stdout)

    def test_import_json_subprocess_with_inline_file_attachment(self):
        pdf_path = Path(self.tmpdir.name) / "inline.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("subprocess-inline"))
        import_path = Path(self.tmpdir.name) / "items-with-attachment.json"
        title = "Imported JSON Attachment"
        import_path.write_text(
            json.dumps(
                [
                    {
                        "itemType": "journalArticle",
                        "title": title,
                        "attachments": [{"path": str(pdf_path)}],
                    }
                ]
            ),
            encoding="utf-8",
        )
        with fake_zotero_http_server(sqlite_path=self.env_paths["sqlite_path"], data_dir=self.env_paths["data_dir"]) as server:
            result = self.run_cli(
                ["--json", "import", "json", str(import_path), "--collection", "COLLAAAA"],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"created_count": 1', result.stdout)

        find_result = self.run_cli(["--json", "item", "find", title, "--exact-title"])
        self.assertEqual(find_result.returncode, 0, msg=find_result.stderr)
        imported_items = json.loads(find_result.stdout)
        self.assertTrue(imported_items)
        imported_item_id = str(imported_items[0]["itemID"])

        attachments_result = self.run_cli(["--json", "item", "attachments", imported_item_id])
        self.assertEqual(attachments_result.returncode, 0, msg=attachments_result.stderr)
        attachments = json.loads(attachments_result.stdout)
        self.assertTrue(attachments)
        self.assertTrue(attachments[0].get("resolvedPath", "").endswith(".pdf"))

        file_result = self.run_cli(["--json", "item", "file", imported_item_id])
        self.assertEqual(file_result.returncode, 0, msg=file_result.stderr)
        item_file = json.loads(file_result.stdout)
        self.assertTrue(item_file.get("exists"))
        self.assertTrue(item_file.get("resolvedPath", "").endswith(".pdf"))

    def test_import_json_subprocess_with_url_attachment(self):
        title = "Imported URL Attachment"
        import_path = Path(self.tmpdir.name) / "items-with-url.json"
        with fake_zotero_http_server(sqlite_path=self.env_paths["sqlite_path"], data_dir=self.env_paths["data_dir"]) as server:
            import_path.write_text(
                json.dumps(
                    [
                        {
                            "itemType": "journalArticle",
                            "title": title,
                            "attachments": [{"url": f"http://127.0.0.1:{server['port']}/downloads/sample.pdf"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                ["--json", "import", "json", str(import_path), "--collection", "COLLAAAA"],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
            attachment_calls = [entry for entry in server["calls"] if entry["path"].startswith("/connector/saveAttachment")]

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"created_count": 1', result.stdout)
        self.assertEqual(len(attachment_calls), 1)
        self.assertEqual(attachment_calls[0]["metadata"]["url"], f"http://127.0.0.1:{server['port']}/downloads/sample.pdf")

    def test_import_file_subprocess_with_attachment_manifest(self):
        ris_path = Path(self.tmpdir.name) / "manifest-import.ris"
        ris_path.write_text("TY  - JOUR\nTI  - Imported Manifest Attachment\nER  - \n", encoding="utf-8")
        pdf_path = Path(self.tmpdir.name) / "manifest.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("manifest"))
        manifest_path = Path(self.tmpdir.name) / "attachments-manifest.json"
        manifest_path.write_text(
            json.dumps([{"index": 0, "attachments": [{"path": str(pdf_path)}]}]),
            encoding="utf-8",
        )
        with fake_zotero_http_server(sqlite_path=self.env_paths["sqlite_path"], data_dir=self.env_paths["data_dir"]) as server:
            result = self.run_cli(
                [
                    "--json",
                    "import",
                    "file",
                    str(ris_path),
                    "--collection",
                    "COLLAAAA",
                    "--attachments-manifest",
                    str(manifest_path),
                ],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"created_count": 1', result.stdout)

    def test_import_json_subprocess_partial_success_returns_nonzero(self):
        pdf_path = Path(self.tmpdir.name) / "partial.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("partial"))
        missing_path = Path(self.tmpdir.name) / "missing.pdf"
        import_path = Path(self.tmpdir.name) / "partial-items.json"
        import_path.write_text(
            json.dumps(
                [
                    {
                        "itemType": "journalArticle",
                        "title": "Imported Partial",
                        "attachments": [
                            {"path": str(pdf_path)},
                            {"path": str(missing_path)},
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        with fake_zotero_http_server(sqlite_path=self.env_paths["sqlite_path"], data_dir=self.env_paths["data_dir"]) as server:
            result = self.run_cli(
                ["--json", "import", "json", str(import_path), "--collection", "COLLAAAA"],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
        self.assertEqual(result.returncode, 1, msg=result.stderr)
        self.assertIn('"status": "partial_success"', result.stdout)
        self.assertIn('"failed_count": 1', result.stdout)

    def test_import_json_subprocess_duplicate_attachment_is_idempotent(self):
        pdf_path = Path(self.tmpdir.name) / "duplicate.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("duplicate"))
        import_path = Path(self.tmpdir.name) / "duplicate-items.json"
        import_path.write_text(
            json.dumps(
                [
                    {
                        "itemType": "journalArticle",
                        "title": "Imported Duplicate Attachment",
                        "attachments": [{"path": str(pdf_path)}, {"path": str(pdf_path)}],
                    }
                ]
            ),
            encoding="utf-8",
        )
        with fake_zotero_http_server(sqlite_path=self.env_paths["sqlite_path"], data_dir=self.env_paths["data_dir"]) as server:
            result = self.run_cli(
                ["--json", "import", "json", str(import_path), "--collection", "COLLAAAA"],
                extra_env={"ZOTERO_HTTP_PORT": str(server["port"])},
            )
            attachment_calls = [entry for entry in server["calls"] if entry["path"].startswith("/connector/saveAttachment")]
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"skipped_count": 1', result.stdout)
        self.assertEqual(len(attachment_calls), 1)

    def test_experimental_collection_write_commands(self):
        create = self.run_cli(["--json", "collection", "create", "Created By CLI", "--experimental"])
        self.assertEqual(create.returncode, 0, msg=create.stderr)
        self.assertIn('"action": "collection_create"', create.stdout)

        add = self.run_cli(["--json", "item", "add-to-collection", "REG12345", "COLLBBBB", "--experimental"])
        self.assertEqual(add.returncode, 0, msg=add.stderr)
        self.assertIn('"action": "item_add_to_collection"', add.stdout)

        move = self.run_cli(
            [
                "--json",
                "item",
                "move-to-collection",
                "REG67890",
                "COLLAAAA",
                "--from",
                "COLLBBBB",
                "--experimental",
            ]
        )
        self.assertEqual(move.returncode, 0, msg=move.stderr)
        self.assertIn('"action": "item_move_to_collection"', move.stdout)
