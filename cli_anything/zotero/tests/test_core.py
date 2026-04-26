from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from cli_anything.zotero.core import analysis, catalog, discovery, docx as docx_mod, docx_static, docx_zoterify, experimental, imports as imports_mod, jsbridge, notes as notes_mod, rendering, session as session_mod
from cli_anything.zotero.tests._helpers import create_sample_environment, fake_zotero_http_server, sample_pdf_bytes
from cli_anything.zotero.utils import openai_api, zotero_http, zotero_paths, zotero_sqlite


def write_docx_with_document_xml(path: Path, body_xml: str) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("word/document.xml", document_xml)


def write_docx_with_zotero_bookmark_fields(path: Path, *, citation_count: int = 1, bibliography_count: int = 1) -> None:
    paragraphs: list[str] = []
    custom_props: list[str] = []
    pid = 2
    for idx in range(citation_count):
        bookmark = f"ZOTERO_BREF_cite{idx}"
        paragraphs.append(
            f"""
            <w:p>
              <w:r><w:t>Claim </w:t></w:r>
              <w:bookmarkStart w:id="{idx}" w:name="{bookmark}"/>
              <w:r><w:t>(Ritchie et al., 2015)</w:t></w:r>
              <w:bookmarkEnd w:id="{idx}"/>
            </w:p>
            """
        )
        custom_props.append(
            f"""
            <property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{pid}" name="{bookmark}_1">
              <vt:lpwstr>ZOTERO_ITEM CSL_CITATION {{&quot;citationItems&quot;:[{{&quot;id&quot;:1}}]}}</vt:lpwstr>
            </property>
            """
        )
        pid += 1
    for idx in range(bibliography_count):
        bookmark = f"ZOTERO_BREF_bibl{idx}"
        paragraphs.append(
            f"""
            <w:p>
              <w:bookmarkStart w:id="{citation_count + idx}" w:name="{bookmark}"/>
              <w:r><w:t>Ritchie, M. E. (2015). Test title.</w:t></w:r>
              <w:bookmarkEnd w:id="{citation_count + idx}"/>
            </w:p>
            """
        )
        custom_props.append(
            f"""
            <property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{pid}" name="{bookmark}_1">
              <vt:lpwstr>ZOTERO_BIBL {{&quot;uncited&quot;:[],&quot;omitted&quot;:[],&quot;custom&quot;:[]}} CSL_BIBLIOGRAPHY</vt:lpwstr>
            </property>
            """
        )
        pid += 1

    write_docx_with_document_xml(path, "".join(paragraphs))
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "docProps/custom.xml",
            (
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
                'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
                + "".join(custom_props)
                + "</Properties>"
            ),
        )


class PathDiscoveryTests(unittest.TestCase):
    def test_build_environment_uses_active_profile_and_data_dir_pref(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir).resolve())
            runtime_env = zotero_paths.build_environment(
                explicit_profile_dir=str(env["profile_root"]),
                explicit_executable=str(env["executable"]),
            )
            self.assertEqual(runtime_env.profile_dir, env["profile_dir"])
            self.assertEqual(runtime_env.data_dir, env["data_dir"])
            self.assertEqual(runtime_env.sqlite_path, env["sqlite_path"])
            self.assertEqual(runtime_env.version, "7.0.32")

    def test_build_environment_accepts_env_profile_dir_pointing_to_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            with mock.patch.dict("os.environ", {"ZOTERO_PROFILE_DIR": str(env["profile_dir"])}, clear=False):
                runtime_env = zotero_paths.build_environment(
                    explicit_executable=str(env["executable"]),
                    explicit_data_dir=str(env["data_dir"]),
                )
            self.assertEqual(runtime_env.profile_dir, env["profile_dir"])

    def test_build_environment_falls_back_to_home_zotero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_root = Path(tmpdir) / "AppData" / "Roaming" / "Zotero" / "Zotero"
            profile_dir = profile_root / "Profiles" / "test.default"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_root / "profiles.ini").write_text("[Profile0]\nName=default\nIsRelative=1\nPath=Profiles/test.default\nDefault=1\n", encoding="utf-8")
            (profile_dir / "prefs.js").write_text("", encoding="utf-8")
            home = Path(tmpdir) / "Home"
            (home / "Zotero").mkdir(parents=True, exist_ok=True)
            with mock.patch("cli_anything.zotero.utils.zotero_paths.Path.home", return_value=home):
                runtime_env = zotero_paths.build_environment(explicit_profile_dir=str(profile_root))
            self.assertEqual(runtime_env.data_dir, home / "Zotero")

    def test_ensure_local_api_enabled_writes_user_js(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            path = zotero_paths.ensure_local_api_enabled(env["profile_dir"])
            self.assertIsNotNone(path)
            self.assertIn('extensions.zotero.httpServer.localAPI.enabled', path.read_text(encoding="utf-8"))

    def test_install_plugin_xpi_supports_zotero_9_patch_releases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            xpi_path = zotero_paths.install_plugin_xpi(env["profile_dir"])

            with zipfile.ZipFile(xpi_path) as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

            zotero_app = manifest["applications"]["zotero"]
            self.assertEqual(manifest["version"], "1.1.0")
            self.assertEqual(zotero_paths.installed_plugin_version(env["profile_dir"]), "1.1.0")
            self.assertEqual(zotero_paths.bundled_plugin_version(), "1.1.0")
            self.assertFalse(zotero_paths.plugin_update_available(env["profile_dir"]))
            self.assertEqual(zotero_app["strict_min_version"], "6.999")
            self.assertEqual(zotero_app["strict_max_version"], "9.0.*")

    def test_find_executable_returns_none_when_unresolved(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("cli_anything.zotero.utils.zotero_paths.shutil.which", return_value=None):
                with mock.patch("pathlib.Path.exists", return_value=False):
                    self.assertIsNone(zotero_paths.find_executable(env={}))


class DocxCitationInspectionTests(unittest.TestCase):
    def test_inspect_citations_detects_endnote_fields_and_static_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "endnote.docx"
            write_docx_with_document_xml(
                path,
                """
                <w:p>
                  <w:r><w:fldChar w:fldCharType="begin"/></w:r>
                  <w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE.DATA </w:instrText></w:r>
                  <w:r><w:fldChar w:fldCharType="end"/></w:r>
                </w:p>
                <w:p><w:r><w:t>Prior work (Ritchie et al., 2015) supports this claim.</w:t></w:r></w:p>
                """,
            )

            report = docx_mod.inspect_citations(path)

        self.assertIn("endnote", report["systems"])
        self.assertIn("static-text", report["systems"])
        self.assertEqual(report["field_counts"]["endnote"], 1)
        self.assertIn("(Ritchie et al., 2015)", report["static_citation_samples"])
        self.assertTrue(any("EndNote fields are present" in note for note in report["notes"]))

    def test_inspect_citations_detects_zotero_and_csl_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "zotero.docx"
            write_docx_with_document_xml(
                path,
                """
                <w:p><w:fldSimple w:instr=" ADDIN ZOTERO_ITEM CSL_CITATION {&quot;citationItems&quot;:[]} ">
                  <w:r><w:t>(Sample, 2026)</w:t></w:r>
                </w:fldSimple></w:p>
                <w:p><w:r><w:instrText xml:space="preserve"> ADDIN CSL_CITATION </w:instrText></w:r></w:p>
                """,
            )

            report = docx_mod.inspect_citations(path)

        self.assertEqual(report["field_counts"]["zotero"], 1)
        self.assertEqual(report["field_counts"]["csl"], 1)
        self.assertEqual(report["field_count"], 2)

    def test_inspect_citations_detects_zotero_bookmark_fields_saved_by_libreoffice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "zotero-bookmark.docx"
            write_docx_with_document_xml(
                path,
                """
                <w:p>
                  <w:r><w:t>Claim </w:t></w:r>
                  <w:bookmarkStart w:id="0" w:name="ZOTERO_BREF_abc123"/>
                  <w:r><w:t>(Ritchie et al., 2015)</w:t></w:r>
                  <w:bookmarkEnd w:id="0"/>
                </w:p>
                """,
            )
            with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "docProps/custom.xml",
                    """
                    <Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
                      xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
                      <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="ZOTERO_BREF_abc123_2">
                        <vt:lpwstr> {&quot;citationItems&quot;:[{&quot;id&quot;:3952}]}</vt:lpwstr>
                      </property>
                      <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="3" name="ZOTERO_BREF_abc123_1">
                        <vt:lpwstr>ZOTERO_ITEM CSL_CITATION</vt:lpwstr>
                      </property>
                    </Properties>
                    """,
                )

            report = docx_mod.inspect_citations(path)

        self.assertEqual(report["field_counts"]["zotero"], 1)
        self.assertEqual(report["field_count"], 1)
        self.assertEqual(report["fields"][0]["field_type"], "bookmark")
        self.assertIn("ZOTERO_ITEM CSL_CITATION", report["fields"][0]["instruction"])
        self.assertIn("static-text", report["systems"])

    def test_inspect_placeholders_detects_zotero_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "placeholders.docx"
            write_docx_with_document_xml(
                path,
                """
                <w:p><w:r><w:t>First claim {{zotero:REG12345}}.</w:t></w:r></w:p>
                <w:p><w:r><w:t>Cluster {{ zotero:REG12345, GROUPKEY }} and invalid {{zotero:not a key}}.</w:t></w:r></w:p>
                """,
            )

            report = docx_mod.inspect_placeholders(path)

        self.assertEqual(report["placeholder_count"], 3)
        self.assertEqual(report["citation_count"], 3)
        self.assertEqual(report["unique_keys"], ["GROUPKEY", "REG12345"])
        self.assertEqual(report["duplicate_keys"], ["REG12345"])
        self.assertEqual(report["invalid_placeholders"][0]["raw"], "{{zotero:not a key}}")

    def test_validate_placeholders_resolves_real_zotero_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            path = Path(tmpdir) / "validate.docx"
            write_docx_with_document_xml(
                path,
                """
                <w:p><w:r><w:t>Known {{zotero:REG12345}} and missing {{zotero:NOITEM99}}.</w:t></w:r></w:p>
                """,
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )

            report = docx_mod.validate_placeholders(runtime, path)

        self.assertFalse(report["ok"])
        self.assertEqual(report["valid_count"], 1)
        self.assertEqual(report["missing_keys"], ["NOITEM99"])
        self.assertEqual(report["items"][0]["key"], "REG12345")
        self.assertEqual(report["items"][0]["title"], "Sample Title")

    def test_render_static_citations_replaces_placeholders_and_appends_bibliography(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "static.docx"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}} and pair {{zotero:REG12345,GROUPKEY}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            with fake_zotero_http_server(sqlite_path=env["sqlite_path"], data_dir=env["data_dir"]) as server:
                runtime.environment.port = server["port"]
                runtime.local_api_available = True
                payload = docx_static.render_static_citations(runtime, source, output, session={}, overwrite=True)

            document_xml = zipfile.ZipFile(output).read("word/document.xml").decode("utf-8")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "static")
        self.assertEqual(payload["placeholder_count"], 2)
        self.assertEqual(payload["bibliography_count"], 2)
        self.assertEqual(payload["inspection"]["field_count"], 0)
        self.assertIn("(REG12345 citation)", document_xml)
        self.assertIn("(REG12345 citation; GROUPKEY citation)", document_xml)
        self.assertIn("REG12345 bibliography", document_xml)
        self.assertIn("GROUPKEY bibliography", document_xml)
        self.assertNotIn("{{zotero:", document_xml)

    def test_render_static_citations_launches_zotero_for_local_api(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "static.docx"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            runtime.local_api_available = False
            with fake_zotero_http_server(sqlite_path=env["sqlite_path"], data_dir=env["data_dir"]) as server:
                runtime.environment.port = server["port"]
                with mock.patch(
                    "cli_anything.zotero.core.discovery.launch_zotero",
                    return_value={"connector_ready": True, "local_api_ready": True},
                ) as launch:
                    payload = docx_static.render_static_citations(runtime, source, output, session={}, overwrite=True)

        launch.assert_called_once()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["zotero_startup"]["attempted"])
        self.assertEqual(payload["inspection"]["field_count"], 0)

    def test_render_static_citations_retries_transient_local_api_startup_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "static.docx"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            runtime.local_api_available = True

            with (
                mock.patch(
                    "cli_anything.zotero.core.docx_static.rendering.citation_item",
                    side_effect=[RuntimeError("Local API returned HTTP 500 for /api/users/0/items/REG12345"), {"citation": "(Retried, 2026)"}],
                ) as citation,
                mock.patch(
                    "cli_anything.zotero.core.docx_static.rendering.bibliography_item",
                    return_value={"bibliography": "Retried bibliography."},
                ),
                mock.patch("cli_anything.zotero.core.docx_static.time.sleep"),
            ):
                payload = docx_static.render_static_citations(runtime, source, output, session={}, overwrite=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(citation.call_count, 2)
        self.assertEqual(payload["rendered_placeholders"][0]["citation"], "(Retried, 2026)")

    def test_zoterify_preflight_reports_ready_when_placeholders_are_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            path = Path(tmpdir) / "ready.docx"
            write_docx_with_document_xml(
                path,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}} and group {{zotero:GROUPKEY}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )

            report = docx_mod.zoterify_preflight(runtime, path, check_external=False)

        self.assertTrue(report["ok"])
        self.assertTrue(report["ready"])
        self.assertEqual(report["checks"]["placeholders"]["citation_count"], 2)
        self.assertEqual(report["checks"]["placeholders"]["missing_keys"], [])
        self.assertTrue(report["checks"]["external"]["skipped"])

    def test_zoterify_preflight_blocks_missing_zotero_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            path = Path(tmpdir) / "missing.docx"
            write_docx_with_document_xml(
                path,
                '<w:p><w:r><w:t>Missing {{zotero:NOITEM99}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )

            report = docx_mod.zoterify_preflight(runtime, path, check_external=False)

        self.assertFalse(report["ok"])
        self.assertFalse(report["ready"])
        self.assertEqual(report["checks"]["placeholders"]["missing_keys"], ["NOITEM99"])
        self.assertTrue(any("resolve to local Zotero items" in note for note in report["notes"]))

    def test_prepare_zotero_import_document_writes_transfer_markers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "transfer.docx"
            write_docx_with_document_xml(
                source,
                (
                    '<w:p><w:r><w:t>Known {{zotero:REG12345}} and group {{zotero:GROUPKEY}}.</w:t></w:r></w:p>'
                    '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
                ),
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )

            report = docx_mod.prepare_zotero_import_document(runtime, source, output, check_external=False)

            document_xml = zipfile.ZipFile(output).read("word/document.xml").decode("utf-8")
            rels_xml = zipfile.ZipFile(output).read("word/_rels/document.xml.rels").decode("utf-8")
            placeholder_count = docx_mod.inspect_placeholders(output)["placeholder_count"]
            root = ET.fromstring(document_xml)
            body_children = list(root.find("w:body", {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}))

        self.assertTrue(report["ok"])
        self.assertEqual(report["citation_count"], 2)
        self.assertIn("ZOTERO_TRANSFER_DOCUMENT", document_xml)
        self.assertIn("ITEM CSL_CITATION", document_xml)
        self.assertIn("DOCUMENT_PREFERENCES", document_xml)
        self.assertIn("http://www.zotero.org/styles/apa", document_xml)
        self.assertIn("https://www.zotero.org/", rels_xml)
        self.assertNotIn("ns0:Relationships", rels_xml)
        self.assertEqual(placeholder_count, 0)
        self.assertIn("sectPr", body_children[-1].tag)
        self.assertNotIn("sectPr", body_children[-2].tag)
        self.assertEqual("ZOTERO_TRANSFER_DOCUMENT", "".join(body_children[0].itertext()))
        self.assertIn("DOCUMENT_PREFERENCES", "".join(body_children[1].itertext()))
        self.assertNotIn("ITEM CSL_CITATION", "".join(body_children[1].itertext()))

    def test_prepare_zotero_import_document_normalizes_short_style_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "transfer.docx"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )

            report = docx_mod.prepare_zotero_import_document(runtime, source, output, style="apa", check_external=False)
            document_xml = zipfile.ZipFile(output).read("word/document.xml").decode("utf-8")

        self.assertEqual(report["style"], "http://www.zotero.org/styles/apa")
        self.assertIn('style id="http://www.zotero.org/styles/apa"', document_xml)
        self.assertIn('&lt;pref name="fieldType" value="ReferenceMark"', document_xml)
        self.assertNotIn('name="noteType"', document_xml)

    def test_build_zoterify_working_docx_replaces_placeholders_with_note_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "working.docx"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}} and pair {{zotero:REG12345,GROUPKEY}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )

            payload = docx_zoterify.build_working_docx(runtime, source, output, session={})
            document_xml = zipfile.ZipFile(output).read("word/document.xml").decode("utf-8")
            rels_xml = zipfile.ZipFile(output).read("word/_rels/document.xml.rels").decode("utf-8")

        self.assertEqual(payload["placeholder_count"], 2)
        self.assertEqual([entry["keys"] for entry in payload["placeholders"]], [["REG12345"], ["REG12345", "GROUPKEY"]])
        self.assertIn("https://www.zotero.org/?", rels_xml)
        self.assertIn("ZOTERO_CLI_PLACEHOLDER_", document_xml)
        self.assertNotIn("{{zotero:", document_xml)

    def test_zoterify_probe_reports_bridge_inactive_without_applescript_fallback(self):
        class InactiveBridge:
            port = 23119

            def bridge_endpoint_active(self):
                return False

        payload = docx_zoterify.zoterify_probe(InactiveBridge())

        self.assertFalse(payload["ready"])
        self.assertFalse(payload["bridge"]["active"])
        self.assertIn("zotero-cli app install-plugin", payload["bridge"]["next_step"])

    def test_zoterify_doctor_reports_optional_workflow_requirements(self):
        class InactiveBridge:
            port = 23119

            def bridge_endpoint_active(self):
                return False

        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            payload = docx_zoterify.zoterify_doctor(runtime, InactiveBridge())

        self.assertFalse(payload["installation_ready"])
        self.assertEqual(payload["workflow"], "optional LibreOffice-backed dynamic DOCX citations")
        self.assertIn("python -m pip install -U cli-anything-zotero", payload["upgrade_steps"])
        self.assertIn("cli_bridge_plugin", payload["requirements"])
        self.assertIn("libreoffice", payload["requirements"])
        self.assertIn("zotero_libreoffice_integration", payload["requirements"])
        self.assertIsNone(payload["probe"])

    def test_zoterify_document_sends_structured_payload_to_bridge(self):
        class RecordingBridge:
            port = 23119

            def __init__(self, output_path):
                self.code = ""
                self.output_path = output_path

            def bridge_endpoint_active(self):
                return True

            def execute_js_http_required(self, code, *, wait_seconds=3):
                self.code = code
                write_docx_with_zotero_bookmark_fields(self.output_path, citation_count=2, bibliography_count=1)
                return {
                    "ok": True,
                    "data": {
                        "ready": True,
                        "converted": True,
                        "field_count": 2,
                        "citation_field_count": 2,
                        "bibliography_field_count": 1,
                        "document_data_written": True,
                        "updated": True,
                    },
                    "error": None,
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "zotero.docx"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}} and group {{zotero:GROUPKEY}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            bridge = RecordingBridge(output)

            payload = docx_zoterify.zoterify_document(runtime, bridge, source, output, open_document=False)

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["ready_for_user"])
        self.assertEqual(payload["backend"], "libreoffice")
        self.assertEqual(payload["converted_placeholders"], 2)
        self.assertEqual(payload["citation_fields"], 2)
        self.assertEqual(payload["bibliography_fields"], 1)
        self.assertTrue(payload["has_zotero_fields"])
        self.assertFalse(payload["saved"])
        self.assertEqual(payload["bibliography"], "auto")
        self.assertEqual(payload["bridge"]["field_count"], 2)
        self.assertIn("convertPlaceholdersToFields", bridge.code)
        self.assertIn('"fieldType": "Bookmark"', bridge.code)
        self.assertIn('"bibliography": {"mode": "auto"', bridge.code)
        self.assertIn('"itemID": 1', bridge.code)
        self.assertIn('"itemID": 5', bridge.code)
        self.assertIn("currentDoc = false", bridge.code)
        self.assertIn("currentCommandPromise = Promise.resolve()", bridge.code)

    def test_zoterify_document_writes_debug_artifacts_only_when_requested(self):
        class RecordingBridge:
            port = 23119

            def __init__(self, output_path):
                self.output_path = output_path

            def bridge_endpoint_active(self):
                return True

            def execute_js_http_required(self, code, *, wait_seconds=3):
                write_docx_with_zotero_bookmark_fields(self.output_path, citation_count=1, bibliography_count=1)
                return {
                    "ok": True,
                    "data": {
                        "ready": True,
                        "converted": True,
                        "field_count": 1,
                        "document_data_written": True,
                        "updated": True,
                    },
                    "error": None,
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "final.docx"
            debug_dir = Path(tmpdir) / "debug"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )

            payload = docx_zoterify.zoterify_document(
                runtime,
                RecordingBridge(output),
                source,
                output,
                open_document=False,
                debug_dir=debug_dir,
            )

            self.assertEqual(payload["artifacts"]["output"], str(output))
            self.assertEqual(payload["artifacts"]["debug_dir"], str(debug_dir))
            self.assertTrue((debug_dir / "01-placeholder-map.json").exists())
            self.assertTrue((debug_dir / "02-bridge-result.json").exists())
            self.assertTrue((debug_dir / "03-inspect-citations.json").exists())

    def test_zoterify_document_warms_up_libreoffice_connection_and_retries(self):
        class WarmingBridge:
            port = 23119

            def __init__(self, output_path):
                self.output_path = output_path
                self.calls = 0

            def bridge_endpoint_active(self):
                return True

            def execute_js_http_required(self, code, *, wait_seconds=3):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "ok": True,
                        "data": {
                            "ready": False,
                            "converted": False,
                            "error": "can't access property \"beginTransaction\", _lastDataListener is undefined",
                        },
                        "error": None,
                    }
                write_docx_with_zotero_bookmark_fields(self.output_path, citation_count=1, bibliography_count=1)
                return {
                    "ok": True,
                    "data": {
                        "ready": True,
                        "converted": True,
                        "field_count": 2,
                        "citation_field_count": 1,
                        "bibliography_field_count": 1,
                        "document_data_written": True,
                        "updated": True,
                    },
                    "error": None,
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "final.docx"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            bridge = WarmingBridge(output)

            with (
                mock.patch.object(docx_zoterify, "_working_output_path", return_value=output),
                mock.patch.object(docx_zoterify, "_open_in_libreoffice", return_value={"attempted": True, "ok": True}),
                mock.patch.object(
                    docx_zoterify,
                    "_warm_up_libreoffice_zotero_connection",
                    return_value={"attempted": True, "ok": True, "method": "test-refresh"},
                ) as warmup,
                mock.patch.object(docx_zoterify, "_save_active_libreoffice_document", return_value={"attempted": True, "ok": True}),
            ):
                payload = docx_zoterify.zoterify_document(runtime, bridge, source, output, open_document=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(bridge.calls, 2)
        warmup.assert_called_once()
        self.assertEqual(payload["libreoffice_warmup"]["method"], "test-refresh")
        self.assertTrue(payload["has_zotero_fields"])

    def test_zoterify_document_launches_zotero_for_bridge_endpoint(self):
        class DelayedBridge:
            port = 23119

            def __init__(self, output_path):
                self.output_path = output_path
                self.active_calls = 0

            def bridge_endpoint_active(self):
                self.active_calls += 1
                return self.active_calls >= 2

            def execute_js_http_required(self, code, *, wait_seconds=3):
                write_docx_with_zotero_bookmark_fields(self.output_path, citation_count=1, bibliography_count=1)
                return {
                    "ok": True,
                    "data": {
                        "ready": True,
                        "converted": True,
                        "field_count": 2,
                        "citation_field_count": 1,
                        "bibliography_field_count": 1,
                        "document_data_written": True,
                        "updated": True,
                    },
                    "error": None,
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            source = Path(tmpdir) / "source.docx"
            output = Path(tmpdir) / "final.docx"
            write_docx_with_document_xml(
                source,
                '<w:p><w:r><w:t>Known {{zotero:REG12345}}.</w:t></w:r></w:p>',
            )
            runtime = discovery.build_runtime_context(
                backend="sqlite",
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            bridge = DelayedBridge(output)

            with mock.patch(
                "cli_anything.zotero.core.discovery.launch_zotero",
                return_value={"connector_ready": True, "local_api_ready": False},
            ) as launch:
                payload = docx_zoterify.zoterify_document(runtime, bridge, source, output, open_document=False)

        launch.assert_called_once()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["zotero_startup"]["attempted"])
        self.assertTrue(payload["has_zotero_fields"])

    def test_normalize_custom_properties_for_word_preserves_zotero_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "zotero.docx"
            write_docx_with_zotero_bookmark_fields(path, citation_count=1, bibliography_count=1)
            before = zipfile.ZipFile(path).read("docProps/custom.xml").decode("utf-8")

            docx_zoterify._normalize_custom_properties_for_word(path)

            after = zipfile.ZipFile(path).read("docProps/custom.xml").decode("utf-8")
            report = docx_mod.inspect_citations(path)

        self.assertIn("&quot;", before)
        self.assertNotIn("&quot;", after)
        self.assertEqual(report["field_counts"]["zotero"], 2)
        self.assertTrue(any("CSL_BIBLIOGRAPHY" in field["instruction"] for field in report["fields"]))


class SQLiteInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.env = create_sample_environment(Path(self.tmpdir.name))

    def test_fetch_libraries(self):
        libraries = zotero_sqlite.fetch_libraries(self.env["sqlite_path"])
        self.assertEqual(len(libraries), 2)
        self.assertEqual([entry["type"] for entry in libraries], ["user", "group"])

    def test_fetch_collections_and_tree(self):
        collections = zotero_sqlite.fetch_collections(self.env["sqlite_path"], library_id=1)
        self.assertIn("Sample Collection", [entry["collectionName"] for entry in collections])
        tree = zotero_sqlite.build_collection_tree(collections)
        self.assertIn("Sample Collection", [entry["collectionName"] for entry in tree])

    def test_resolve_item_includes_fields_creators_tags(self):
        item = zotero_sqlite.resolve_item(self.env["sqlite_path"], "REG12345")
        self.assertEqual(item["title"], "Sample Title")
        self.assertEqual(item["fields"]["title"], "Sample Title")
        self.assertEqual(item["creators"][0]["lastName"], "Lovelace")
        self.assertEqual(item["tags"][0]["name"], "sample-tag")

    def test_fetch_item_children_and_attachments(self):
        children = zotero_sqlite.fetch_item_children(self.env["sqlite_path"], "REG12345")
        self.assertEqual(len(children), 2)
        attachments = zotero_sqlite.fetch_item_attachments(self.env["sqlite_path"], "REG12345")
        self.assertEqual(len(attachments), 1)
        resolved = zotero_sqlite.resolve_attachment_real_path(attachments[0], self.env["data_dir"])
        self.assertTrue(str(resolved).endswith("paper.pdf"))

        linked_attachments = zotero_sqlite.fetch_item_attachments(self.env["sqlite_path"], "REG67890")
        self.assertEqual(len(linked_attachments), 1)
        linked_resolved = zotero_sqlite.resolve_attachment_real_path(linked_attachments[0], self.env["data_dir"])
        self.assertEqual(linked_resolved, "C:\\Users\\Public\\linked.pdf")

    def test_duplicate_key_resolution_requires_library_context(self):
        with self.assertRaises(zotero_sqlite.AmbiguousReferenceError):
            zotero_sqlite.resolve_item(self.env["sqlite_path"], "DUPITEM1")
        with self.assertRaises(zotero_sqlite.AmbiguousReferenceError):
            zotero_sqlite.resolve_collection(self.env["sqlite_path"], "DUPCOLL1")
        with self.assertRaises(zotero_sqlite.AmbiguousReferenceError):
            zotero_sqlite.resolve_saved_search(self.env["sqlite_path"], "DUPSEARCH")

        user_item = zotero_sqlite.resolve_item(self.env["sqlite_path"], "DUPITEM1", library_id=1)
        group_item = zotero_sqlite.resolve_item(self.env["sqlite_path"], "DUPITEM1", library_id=2)
        self.assertEqual(user_item["title"], "User Duplicate Title")
        self.assertEqual(group_item["title"], "Group Duplicate Title")

        group_collection = zotero_sqlite.resolve_collection(self.env["sqlite_path"], "DUPCOLL1", library_id=2)
        self.assertEqual(group_collection["collectionName"], "Group Duplicate Collection")

        group_search = zotero_sqlite.resolve_saved_search(self.env["sqlite_path"], "DUPSEARCH", library_id=2)
        self.assertEqual(group_search["savedSearchName"], "Group Duplicate Search")

    def test_cross_library_unique_key_still_resolves_without_session_context(self):
        group_item = zotero_sqlite.resolve_item(self.env["sqlite_path"], "GROUPKEY")
        self.assertEqual(group_item["libraryID"], 2)
        group_collection = zotero_sqlite.resolve_collection(self.env["sqlite_path"], "GCOLLAAA")
        self.assertEqual(group_collection["libraryID"], 2)

    def test_fetch_saved_searches_and_tags(self):
        searches = zotero_sqlite.fetch_saved_searches(self.env["sqlite_path"], library_id=1)
        self.assertEqual(searches[0]["savedSearchName"], "Important")
        tags = zotero_sqlite.fetch_tags(self.env["sqlite_path"], library_id=1)
        self.assertEqual(tags[0]["name"], "sample-tag")
        items = zotero_sqlite.fetch_tag_items(self.env["sqlite_path"], "sample-tag", library_id=1)
        self.assertGreaterEqual(len(items), 1)

    def test_find_collections_and_items_and_notes(self):
        collections = zotero_sqlite.find_collections(self.env["sqlite_path"], "collection", library_id=1, limit=10)
        self.assertGreaterEqual(len(collections), 2)
        self.assertIn("Archive Collection", [entry["collectionName"] for entry in collections])

        fuzzy_items = zotero_sqlite.find_items_by_title(self.env["sqlite_path"], "Sample", library_id=1, limit=10)
        self.assertEqual(fuzzy_items[0]["key"], "REG12345")
        exact_items = zotero_sqlite.find_items_by_title(self.env["sqlite_path"], "Sample Title", library_id=1, exact_title=True, limit=10)
        self.assertEqual(exact_items[0]["itemID"], 1)

        notes = zotero_sqlite.fetch_item_notes(self.env["sqlite_path"], "REG12345")
        self.assertEqual(notes[0]["typeName"], "note")
        self.assertEqual(notes[0]["noteText"], "Example note")

    def test_experimental_sqlite_write_helpers(self):
        created = zotero_sqlite.create_collection_record(self.env["sqlite_path"], name="Created Here", library_id=1, parent_collection_id=1)
        self.assertEqual(created["collectionName"], "Created Here")
        self.assertTrue(Path(created["backupPath"]).exists())

        added = zotero_sqlite.add_item_to_collection_record(self.env["sqlite_path"], item_id=1, collection_id=2)
        self.assertTrue(Path(added["backupPath"]).exists())

        moved = zotero_sqlite.move_item_between_collections_record(
            self.env["sqlite_path"],
            item_id=4,
            target_collection_id=1,
            source_collection_ids=[2],
        )
        self.assertTrue(Path(moved["backupPath"]).exists())
        memberships = zotero_sqlite.fetch_item_collections(self.env["sqlite_path"], 4)
        self.assertEqual([membership["collectionID"] for membership in memberships], [1])


class SessionTests(unittest.TestCase):
    def test_save_and_load_session_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict("os.environ", {"CLI_ANYTHING_ZOTERO_STATE_DIR": tmpdir}, clear=False):
                state = session_mod.default_session_state()
                state["current_item"] = "REG12345"
                session_mod.save_session_state(state)
                loaded = session_mod.load_session_state()
                self.assertEqual(loaded["current_item"], "REG12345")

    def test_expand_repl_aliases(self):
        state = {"current_library": "1", "current_collection": "2", "current_item": "REG12345"}
        expanded = session_mod.expand_repl_aliases_with_state(["item", "get", "@item", "@collection"], state)
        self.assertEqual(expanded, ["item", "get", "REG12345", "2"])

    def test_normalize_library_ref_accepts_plain_and_tree_view_ids(self):
        self.assertEqual(zotero_sqlite.normalize_library_ref("1"), 1)
        self.assertEqual(zotero_sqlite.normalize_library_ref("L1"), 1)
        self.assertEqual(zotero_sqlite.normalize_library_ref(2), 2)


class HttpUtilityTests(unittest.TestCase):
    def test_build_runtime_context_reports_unavailable_services(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            prefs_path = env["profile_dir"] / "prefs.js"
            prefs_text = prefs_path.read_text(encoding="utf-8").replace("23119", "23191")
            prefs_path.write_text(prefs_text, encoding="utf-8")
            runtime = discovery.build_runtime_context(
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            self.assertFalse(runtime.connector_available)
            self.assertFalse(runtime.local_api_available)

    def test_catalog_style_list_parses_csl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            runtime = discovery.build_runtime_context(
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            styles = catalog.list_styles(runtime)
            self.assertEqual(styles[0]["title"], "Sample Style")

    def test_wait_for_endpoint_requires_explicit_ready_status(self):
        with fake_zotero_http_server(local_api_root_status=403) as server:
            ready = zotero_http.wait_for_endpoint(
                server["port"],
                "/api/",
                timeout=1,
                poll_interval=0.05,
                headers={"Zotero-API-Version": zotero_http.LOCAL_API_VERSION},
            )
        self.assertFalse(ready)

        with fake_zotero_http_server(local_api_root_status=200) as server:
            ready = zotero_http.wait_for_endpoint(
                server["port"],
                "/api/",
                timeout=1,
                poll_interval=0.05,
                headers={"Zotero-API-Version": zotero_http.LOCAL_API_VERSION},
            )
        self.assertTrue(ready)

    def test_launch_zotero_raises_when_executable_is_unresolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            runtime = discovery.build_runtime_context(
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            runtime.environment.executable = None
            with self.assertRaisesRegex(RuntimeError, "could not be resolved"):
                discovery.launch_zotero(runtime)

    def test_launch_zotero_opens_macos_app_bundle_from_inner_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            app_bundle = Path(tmpdir) / "Applications" / "Zotero.app"
            executable = app_bundle / "Contents" / "MacOS" / "zotero"
            executable.parent.mkdir(parents=True)
            executable.write_text("", encoding="utf-8")
            runtime = discovery.build_runtime_context(
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(executable),
            )
            runtime.environment.local_api_enabled_configured = True

            with mock.patch("sys.platform", "darwin"):
                with mock.patch("cli_anything.zotero.core.discovery.subprocess.Popen") as popen:
                    with mock.patch("cli_anything.zotero.core.discovery.zotero_http.wait_for_endpoint", side_effect=[True, True]) as wait:
                        popen.return_value.pid = 123
                        payload = discovery.launch_zotero(runtime)

            popen.assert_called_once_with(["open", str(app_bundle)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertEqual(wait.call_count, 2)
            self.assertTrue(payload["connector_ready"])
            self.assertTrue(payload["local_api_ready"])

    def test_ensure_local_api_ready_launches_zotero_when_unavailable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            runtime = discovery.build_runtime_context(
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            runtime.local_api_available = False

            with mock.patch(
                "cli_anything.zotero.core.discovery.launch_zotero",
                return_value={"connector_ready": True, "local_api_ready": True},
            ) as launch:
                payload = discovery.ensure_local_api_ready(runtime)

        launch.assert_called_once()
        self.assertTrue(payload["ok"])
        self.assertTrue(runtime.local_api_available)

    def test_ensure_bridge_endpoint_ready_launches_zotero_and_waits_for_bridge(self):
        class DelayedBridge:
            def __init__(self):
                self.calls = 0

            def bridge_endpoint_active(self):
                self.calls += 1
                return self.calls >= 3

        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            runtime = discovery.build_runtime_context(
                data_dir=str(env["data_dir"]),
                profile_dir=str(env["profile_dir"]),
                executable=str(env["executable"]),
            )
            bridge = DelayedBridge()

            with mock.patch(
                "cli_anything.zotero.core.discovery.launch_zotero",
                return_value={"connector_ready": True, "local_api_ready": False},
            ) as launch:
                payload = discovery.ensure_bridge_endpoint_ready(runtime, bridge, wait_timeout=1, poll_interval=0.01)

        launch.assert_called_once()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["endpoint_active"])


class ImportCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.env = create_sample_environment(Path(self.tmpdir.name))
        self.runtime = discovery.build_runtime_context(
            data_dir=str(self.env["data_dir"]),
            profile_dir=str(self.env["profile_dir"]),
            executable=str(self.env["executable"]),
        )

    def test_enable_local_api_reports_idempotent_state(self):
        payload = imports_mod.enable_local_api(self.runtime)
        self.assertTrue(payload["enabled"])
        self.assertFalse(payload["already_enabled"])
        self.assertTrue(Path(payload["user_js_path"]).exists())

        refreshed = discovery.build_runtime_context(
            data_dir=str(self.env["data_dir"]),
            profile_dir=str(self.env["profile_dir"]),
            executable=str(self.env["executable"]),
        )
        second = imports_mod.enable_local_api(refreshed)
        self.assertTrue(second["already_enabled"])

    def test_import_json_uses_session_collection_and_tags(self):
        json_path = Path(self.tmpdir.name) / "items.json"
        json_path.write_text('[{"itemType": "journalArticle", "title": "Imported"}]', encoding="utf-8")

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_items") as save_items:
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session") as update_session:
                    payload = imports_mod.import_json(
                        self.runtime,
                        json_path,
                        tags=["alpha", "beta"],
                        session={"current_collection": "COLLAAAA"},
                    )

        save_items.assert_called_once()
        submitted_items = save_items.call_args.args[1]
        self.assertEqual(submitted_items[0]["title"], "Imported")
        self.assertTrue(submitted_items[0]["id"].startswith("cli-anything-zotero-"))
        update_session.assert_called_once()
        self.assertEqual(update_session.call_args.kwargs["target"], "C1")
        self.assertEqual(update_session.call_args.kwargs["tags"], ["alpha", "beta"])
        self.assertEqual(payload["submitted_count"], 1)
        self.assertEqual(payload["target"]["treeViewID"], "C1")

    def test_import_file_posts_raw_text_and_explicit_tree_view_target(self):
        ris_path = Path(self.tmpdir.name) / "sample.ris"
        ris_path.write_text("TY  - JOUR\nTI  - Imported Title\nER  - \n", encoding="utf-8")

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.connector_import_text", return_value=[{"title": "Imported Title"}]) as import_text:
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session") as update_session:
                    payload = imports_mod.import_file(
                        self.runtime,
                        ris_path,
                        collection_ref="C99",
                        tags=["imported"],
                    )

        import_text.assert_called_once()
        self.assertIn("Imported Title", import_text.call_args.args[1])
        update_session.assert_called_once()
        self.assertEqual(update_session.call_args.kwargs["target"], "C99")
        self.assertEqual(payload["imported_count"], 1)

    def test_import_json_strips_inline_attachments_and_uploads_local_pdf(self):
        pdf_path = Path(self.tmpdir.name) / "inline.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("inline"))
        json_path = Path(self.tmpdir.name) / "items.json"
        json_path.write_text(
            '[{"itemType": "journalArticle", "title": "Imported", "attachments": [{"path": "%s"}]}]' % str(pdf_path).replace("\\", "\\\\"),
            encoding="utf-8",
        )

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_items") as save_items:
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session"):
                    with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_attachment") as save_attachment:
                        payload = imports_mod.import_json(
                            self.runtime,
                            json_path,
                            collection_ref="C1",
                            attachment_timeout=91,
                        )

        submitted_items = save_items.call_args.args[1]
        self.assertNotIn("attachments", submitted_items[0])
        self.assertEqual(payload["attachment_summary"]["created_count"], 1)
        self.assertEqual(payload["status"], "success")
        save_attachment.assert_called_once()
        self.assertEqual(save_attachment.call_args.kwargs["parent_item_id"], submitted_items[0]["id"])
        self.assertEqual(save_attachment.call_args.kwargs["timeout"], 91)
        self.assertTrue(save_attachment.call_args.kwargs["url"].startswith("file:///"))
        self.assertTrue(save_attachment.call_args.kwargs["content"].startswith(b"%PDF-"))

    def test_import_json_url_attachment_uses_delay_and_default_timeout(self):
        json_path = Path(self.tmpdir.name) / "items.json"
        with fake_zotero_http_server() as server:
            json_path.write_text(
                json.dumps(
                    [
                        {
                            "itemType": "journalArticle",
                            "title": "Imported URL",
                            "attachments": [
                                {
                                    "url": f"http://127.0.0.1:{server['port']}/downloads/wrong-content-type.pdf",
                                    "delay_ms": 10,
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch.object(self.runtime, "connector_available", True):
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_items"):
                    with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session"):
                        with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_attachment") as save_attachment:
                            with mock.patch("cli_anything.zotero.core.imports.time.sleep") as sleep:
                                payload = imports_mod.import_json(
                                    self.runtime,
                                    json_path,
                                    collection_ref="C1",
                                    attachment_timeout=47,
                                )

        sleep.assert_called_once_with(0.01)
        save_attachment.assert_called_once()
        self.assertEqual(save_attachment.call_args.kwargs["timeout"], 47)
        self.assertEqual(payload["attachment_summary"]["created_count"], 1)

    def test_import_json_duplicate_inline_attachments_are_skipped(self):
        pdf_path = Path(self.tmpdir.name) / "duplicate.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("duplicate"))
        json_path = Path(self.tmpdir.name) / "items.json"
        json_path.write_text(
            json.dumps(
                [
                    {
                        "itemType": "journalArticle",
                        "title": "Imported Duplicate",
                        "attachments": [
                            {"path": str(pdf_path)},
                            {"path": str(pdf_path)},
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_items"):
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session"):
                    with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_attachment") as save_attachment:
                        payload = imports_mod.import_json(self.runtime, json_path, collection_ref="C1")

        save_attachment.assert_called_once()
        self.assertEqual(payload["attachment_summary"]["created_count"], 1)
        self.assertEqual(payload["attachment_summary"]["skipped_count"], 1)
        self.assertEqual(payload["attachment_results"][1]["status"], "skipped_duplicate")

    def test_import_json_rejects_invalid_inline_attachment_schema(self):
        json_path = Path(self.tmpdir.name) / "invalid-attachments.json"
        json_path.write_text(
            json.dumps(
                [
                    {
                        "itemType": "journalArticle",
                        "title": "Broken",
                        "attachments": [{"path": "a.pdf", "url": "https://example.com/a.pdf"}],
                    }
                ]
            ),
            encoding="utf-8",
        )
        with mock.patch.object(self.runtime, "connector_available", True):
            with self.assertRaises(RuntimeError):
                imports_mod.import_json(self.runtime, json_path)

    def test_import_file_manifest_partial_success_records_attachment_failures(self):
        ris_path = Path(self.tmpdir.name) / "sample.ris"
        ris_path.write_text("TY  - JOUR\nTI  - Imported Title\nER  - \n", encoding="utf-8")
        pdf_path = Path(self.tmpdir.name) / "manifest.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("manifest"))
        manifest_path = Path(self.tmpdir.name) / "attachments.json"
        manifest_path.write_text(
            json.dumps(
                [
                    {
                        "index": 0,
                        "attachments": [
                            {"path": str(pdf_path)},
                            {"path": str(Path(self.tmpdir.name) / "missing.pdf")},
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch(
                "cli_anything.zotero.utils.zotero_http.connector_import_text",
                return_value=[{"id": "imported-1", "title": "Imported Title"}],
            ):
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session"):
                    with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_attachment") as save_attachment:
                        payload = imports_mod.import_file(
                            self.runtime,
                            ris_path,
                            collection_ref="C1",
                            attachments_manifest=manifest_path,
                        )

        save_attachment.assert_called_once()
        self.assertEqual(payload["status"], "partial_success")
        self.assertEqual(payload["attachment_summary"]["created_count"], 1)
        self.assertEqual(payload["attachment_summary"]["failed_count"], 1)
        self.assertIn("Attachment file not found", payload["attachment_results"][1]["error"])

    def test_import_file_manifest_title_mismatch_marks_attachment_failure(self):
        ris_path = Path(self.tmpdir.name) / "sample.ris"
        ris_path.write_text("TY  - JOUR\nTI  - Imported Title\nER  - \n", encoding="utf-8")
        pdf_path = Path(self.tmpdir.name) / "manifest.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("manifest"))
        manifest_path = Path(self.tmpdir.name) / "attachments.json"
        manifest_path.write_text(
            json.dumps(
                [
                    {
                        "index": 0,
                        "expected_title": "Different Title",
                        "attachments": [{"path": str(pdf_path)}],
                    }
                ]
            ),
            encoding="utf-8",
        )

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch(
                "cli_anything.zotero.utils.zotero_http.connector_import_text",
                return_value=[{"id": "imported-1", "title": "Imported Title"}],
            ):
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session"):
                    with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_attachment") as save_attachment:
                        payload = imports_mod.import_file(
                            self.runtime,
                            ris_path,
                            collection_ref="C1",
                            attachments_manifest=manifest_path,
                        )

        save_attachment.assert_not_called()
        self.assertEqual(payload["status"], "partial_success")
        self.assertIn("title mismatch", payload["attachment_results"][0]["error"])

    def test_import_file_manifest_index_out_of_range_and_missing_connector_id_fail_cleanly(self):
        ris_path = Path(self.tmpdir.name) / "sample.ris"
        ris_path.write_text("TY  - JOUR\nTI  - Imported Title\nER  - \n", encoding="utf-8")
        pdf_path = Path(self.tmpdir.name) / "manifest.pdf"
        pdf_path.write_bytes(sample_pdf_bytes("manifest"))
        manifest_path = Path(self.tmpdir.name) / "attachments.json"
        manifest_path.write_text(
            json.dumps(
                [
                    {"index": 1, "attachments": [{"path": str(pdf_path)}]},
                    {"index": 0, "attachments": [{"path": str(pdf_path)}]},
                ]
            ),
            encoding="utf-8",
        )

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch(
                "cli_anything.zotero.utils.zotero_http.connector_import_text",
                return_value=[{"title": "Imported Title"}],
            ):
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session"):
                    with mock.patch("cli_anything.zotero.utils.zotero_http.connector_save_attachment") as save_attachment:
                        payload = imports_mod.import_file(
                            self.runtime,
                            ris_path,
                            collection_ref="C1",
                            attachments_manifest=manifest_path,
                        )

        save_attachment.assert_not_called()
        self.assertEqual(payload["attachment_summary"]["failed_count"], 2)
        self.assertIn("index 1", payload["attachment_results"][0]["error"])
        self.assertIn("did not include a connector id", payload["attachment_results"][1]["error"])

    def test_import_json_rejects_invalid_json(self):
        json_path = Path(self.tmpdir.name) / "bad.json"
        json_path.write_text("{not-valid", encoding="utf-8")
        with mock.patch.object(self.runtime, "connector_available", True):
            with self.assertRaises(RuntimeError):
                imports_mod.import_json(self.runtime, json_path)

    def test_import_requires_connector(self):
        json_path = Path(self.tmpdir.name) / "items.json"
        json_path.write_text("[]", encoding="utf-8")
        with mock.patch.object(self.runtime, "connector_available", False):
            with self.assertRaises(RuntimeError):
                imports_mod.import_json(self.runtime, json_path)


class WorkflowCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.env = create_sample_environment(Path(self.tmpdir.name))
        self.runtime = discovery.build_runtime_context(
            data_dir=str(self.env["data_dir"]),
            profile_dir=str(self.env["profile_dir"]),
            executable=str(self.env["executable"]),
        )

    def test_collection_find_and_item_find_sqlite_fallback(self):
        collections = catalog.find_collections(self.runtime, "sample", limit=10)
        self.assertEqual(collections[0]["key"], "COLLAAAA")

        with mock.patch.object(self.runtime, "local_api_available", False):
            items = catalog.find_items(self.runtime, "Sample", limit=10, session={})
        self.assertEqual(items[0]["key"], "REG12345")

        exact = catalog.find_items(self.runtime, "Sample Title", exact_title=True, limit=10, session={})
        self.assertEqual(exact[0]["itemID"], 1)

    def test_collection_scoped_item_find_prefers_local_api(self):
        with mock.patch.object(self.runtime, "local_api_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.local_api_get_json", return_value=[{"key": "REG12345"}]) as local_api:
                items = catalog.find_items(self.runtime, "Sample", collection_ref="COLLAAAA", limit=5, session={})
        local_api.assert_called_once()
        self.assertEqual(items[0]["key"], "REG12345")

    def test_item_find_passes_quick_search_scope_to_local_api(self):
        with mock.patch.object(self.runtime, "local_api_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.local_api_get_json", return_value=[{"key": "REG12345"}]) as local_api:
                items = catalog.find_items(self.runtime, "tag value", limit=5, search_scope="fields", session={})
        self.assertEqual(items[0]["key"], "REG12345")
        self.assertEqual(local_api.call_args.kwargs["params"]["qmode"], "fields")

    def test_group_library_local_api_scope_and_search_routes(self):
        self.assertEqual(catalog.local_api_scope(self.runtime, 1), "/api/users/0")
        self.assertEqual(catalog.local_api_scope(self.runtime, 2), "/api/groups/2")

        with mock.patch.object(self.runtime, "local_api_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.local_api_get_json", return_value=[{"key": "GROUPKEY"}]) as local_api:
                items = catalog.find_items(
                    self.runtime,
                    "Group",
                    collection_ref="GCOLLAAA",
                    limit=5,
                    session={"current_library": 2},
                )
        self.assertEqual(items[0]["libraryID"], 2)
        self.assertIn("/api/groups/2/collections/GCOLLAAA/items/top", local_api.call_args.args[1])

        with mock.patch.object(self.runtime, "local_api_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.local_api_get_json", return_value=[{"key": "GROUPKEY"}]) as local_api:
                payload = catalog.search_items(self.runtime, "GSEARCHKEY", session={"current_library": 2})
        self.assertEqual(payload[0]["key"], "GROUPKEY")
        self.assertIn("/api/groups/2/searches/GSEARCHKEY/items", local_api.call_args.args[1])

    def test_item_notes_and_note_get(self):
        item_notes = catalog.item_notes(self.runtime, "REG12345")
        self.assertEqual(len(item_notes), 1)
        self.assertEqual(item_notes[0]["notePreview"], "Example note")

        note = notes_mod.get_note(self.runtime, "NOTEKEY")
        self.assertEqual(note["noteText"], "Example note")

    def test_note_add_builds_child_note_payload(self):
        js_response = {"ok": True, "data": {"key": "NEWNOTE1", "itemID": 9999, "title": "Sample Title"}}
        with mock.patch.object(jsbridge.JSBridgeClient, "execute_js", return_value=js_response) as exec_js:
            payload = notes_mod.add_note(
                self.runtime,
                "REG12345",
                text="# Heading\n\nA **bold** note",
                fmt="markdown",
            )
        exec_js.assert_called_once()
        js_code = exec_js.call_args.args[0]
        self.assertIn("setNote(", js_code)
        self.assertIn("<h1>", js_code)
        self.assertEqual(payload["parentItemKey"], "REG12345")
        self.assertEqual(payload["key"], "NEWNOTE1")
        self.assertEqual(payload["action"], "note_add")

    def test_item_context_aggregates_exports_and_links(self):
        with mock.patch.object(self.runtime, "local_api_available", True):
            with mock.patch("cli_anything.zotero.core.rendering.export_item", side_effect=[{"content": "@article{sample}"}, {"content": '{"id":"sample"}'}]):
                payload = analysis.build_item_context(
                    self.runtime,
                    "REG12345",
                    include_notes=True,
                    include_bibtex=True,
                    include_csljson=True,
                    include_links=True,
                )
        self.assertEqual(payload["links"]["doi_url"], "https://doi.org/10.1000/sample")
        self.assertIn("bibtex", payload["exports"])
        self.assertIn("Notes:", payload["prompt_context"])

    def test_item_analyze_requires_api_key_and_uses_openai(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                analysis.analyze_item(self.runtime, "REG12345", question="Summarize", model="gpt-test")

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False):
            with mock.patch("cli_anything.zotero.core.analysis.build_item_context", return_value={"item": {"key": "REG12345"}, "prompt_context": "Title: Sample"}):
                with mock.patch("cli_anything.zotero.utils.openai_api.create_text_response", return_value={"response_id": "resp_123", "answer": "Analysis", "raw": {}}) as create_response:
                    payload = analysis.analyze_item(self.runtime, "REG12345", question="Summarize", model="gpt-test")
        create_response.assert_called_once()
        self.assertEqual(payload["answer"], "Analysis")

    def test_experimental_commands_require_closed_zotero_and_update_db_copy(self):
        with mock.patch.object(self.runtime, "connector_available", True):
            with self.assertRaises(RuntimeError):
                experimental.create_collection(self.runtime, "Blocked")

        with mock.patch.object(self.runtime, "connector_available", False):
            created = experimental.create_collection(self.runtime, "Created")
            self.assertEqual(created["action"], "collection_create")

            added = experimental.add_item_to_collection(self.runtime, "REG12345", "COLLBBBB")
            self.assertEqual(added["action"], "item_add_to_collection")

            moved = experimental.move_item_to_collection(
                self.runtime,
                "REG67890",
                "COLLAAAA",
                from_refs=["COLLBBBB"],
            )
        self.assertEqual(moved["action"], "item_move_to_collection")

    def test_rendering_uses_group_library_local_api_scope(self):
        with mock.patch.object(self.runtime, "local_api_available", True):
            with mock.patch("cli_anything.zotero.utils.zotero_http.local_api_get_text", return_value="TY  - JOUR\nER  - \n") as get_text:
                export_payload = rendering.export_item(self.runtime, "GROUPKEY", "ris", session={"current_library": 2})
        self.assertEqual(export_payload["libraryID"], 2)
        self.assertIn("/api/groups/2/items/GROUPKEY", get_text.call_args.args[1])


class OpenAIUtilityTests(unittest.TestCase):
    def test_extract_text_from_response_payload(self):
        payload = {
            "id": "resp_1",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Hello world"},
                    ],
                }
            ],
        }
        result = openai_api._extract_text(payload)
        self.assertEqual(result, "Hello world")
