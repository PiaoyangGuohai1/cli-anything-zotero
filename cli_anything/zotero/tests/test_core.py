from __future__ import annotations

import json
import sqlite3
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

    def test_install_plugin_xpi_supports_current_and_future_zotero_majors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            xpi_path = zotero_paths.install_plugin_xpi(env["profile_dir"])

            with zipfile.ZipFile(xpi_path) as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

            zotero_app = manifest["applications"]["zotero"]
            self.assertEqual(manifest["version"], "1.4.0")
            self.assertEqual(zotero_paths.installed_plugin_version(env["profile_dir"]), "1.4.0")
            self.assertEqual(zotero_paths.bundled_plugin_version(), "1.4.0")
            self.assertFalse(zotero_paths.plugin_update_available(env["profile_dir"]))
            self.assertEqual(zotero_app["strict_min_version"], "6.999")
            # Zotero 10 disables plugins whose max version is below the running major.
            self.assertEqual(zotero_app["strict_max_version"], "9999.*")

    def test_plugin_app_disabled_detects_zotero_disabled_plugin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            profile_dir = env["profile_dir"]
            self.assertFalse(zotero_paths.plugin_app_disabled(profile_dir))

            (profile_dir / "extensions.json").write_text(
                json.dumps({"addons": [{"id": zotero_paths.PLUGIN_ADDON_ID, "appDisabled": True}]}),
                encoding="utf-8",
            )
            self.assertTrue(zotero_paths.plugin_app_disabled(profile_dir))

    def test_fetch_saved_searches_tolerates_missing_required_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = create_sample_environment(Path(tmpdir))
            sqlite_path = env["data_dir"] / "zotero.sqlite"
            # Zotero 10 dropped savedSearchConditions.required.
            with sqlite3.connect(sqlite_path) as conn:
                conn.execute("ALTER TABLE savedSearchConditions DROP COLUMN required")

            searches = zotero_sqlite.fetch_saved_searches(sqlite_path, library_id=1)
            self.assertTrue(searches)
            conditions = searches[0]["conditions"]
            self.assertTrue(conditions)
            self.assertIsNone(conditions[0]["required"])

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

    def test_session_library_id_handles_none_default_session(self):
        # Fresh session always includes current_library: None — must not int(None).
        self.assertEqual(session_mod.session_library_id(session_mod.default_session_state()), 1)
        self.assertEqual(session_mod.session_library_id({"current_library": None}), 1)
        self.assertEqual(session_mod.session_library_id({}), 1)
        self.assertEqual(session_mod.session_library_id({"current_library": 2}), 2)
        self.assertEqual(session_mod.session_library_id({"current_library": "3"}), 3)
        self.assertEqual(session_mod.session_library_id(None, default=1), 1)

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

    def test_normalize_doi_strips_url_and_punctuation(self):
        self.assertEqual(
            imports_mod.normalize_doi("https://doi.org/10.1038/s41592-024-02201-0."),
            "10.1038/s41592-024-02201-0",
        )
        self.assertEqual(imports_mod.normalize_doi("doi:10.1/abc"), "10.1/abc")

    def test_split_bibtex_entries(self):
        content = "@article{a, title={A},}\n\n@article{b, title={B},}\n"
        entries = imports_mod._split_bibtex_entries(content)
        self.assertEqual(len(entries), 2)
        self.assertIn("@article{a", entries[0])
        self.assertIn("@article{b", entries[1])
        self.assertEqual(imports_mod._count_bibtex_entries(content), 2)

    def test_application_import_payload_rejects_empty_success(self):
        payload = imports_mod._application_import_payload({"ok": True, "data": None, "error": None})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "EMPTY_RESULT")

    def test_application_import_payload_reads_structured_success(self):
        payload = imports_mod._application_import_payload(
            {"ok": True, "data": {"ok": True, "key": "ABCDEFGH", "title": "Paper"}, "error": None}
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["key"], "ABCDEFGH")

    def test_format_bridge_error_never_returns_empty_object(self):
        from cli_anything.zotero.core import jsbridge

        self.assertEqual(jsbridge._format_bridge_error({}), "unknown bridge error")
        self.assertEqual(jsbridge._format_bridge_error({"error": "boom"}), "boom")
        self.assertEqual(jsbridge._format_bridge_error(None), "unknown bridge error")

    def test_import_doi_falls_back_to_crossref_bibtex(self):
        class FakeBridge:
            def find_items_by_doi(self, doi, *, library_id=1, limit=20):
                return {"ok": True, "data": [], "error": None}

            def import_from_doi(self, doi, *, collection_key=None, tags=None, library_id=1):
                return {
                    "ok": True,
                    "data": {
                        "ok": False,
                        "code": "TRANSLATOR_EMPTY",
                        "error": "No items returned from any translator",
                    },
                    "error": None,
                }

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch(
                "cli_anything.zotero.core.imports.fetch_crossref_bibtex",
                return_value="@article{X, title={Fallback Title}, DOI={10.1038/s41592-024-02201-0}}",
            ):
                with mock.patch(
                    "cli_anything.zotero.utils.zotero_http.connector_import_text",
                    return_value=[{"key": "FALLBACK1", "title": "Fallback Title"}],
                ) as import_text:
                    with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session") as update_session:
                        payload = imports_mod.import_doi(
                            self.runtime,
                            FakeBridge(),
                            "https://doi.org/10.1038/s41592-024-02201-0",
                            collection_key="COLLAAAA",
                            tags=["virtual-cell"],
                        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "crossref-bibtex")
        self.assertEqual(payload["key"], "FALLBACK1")
        self.assertEqual(payload["status"], "success")
        import_text.assert_called_once()
        self.assertEqual(import_text.call_args.kwargs.get("content_type"), "text/x-bibtex")
        update_session.assert_called_once()
        self.assertEqual(update_session.call_args.kwargs["tags"], ["virtual-cell"])

    def test_import_doi_dedupes_existing_library_item(self):
        class FakeBridge:
            def find_items_by_doi(self, doi, *, library_id=1, limit=20):
                return {
                    "ok": True,
                    "data": [{"key": "EXISTING1", "title": "Already Here", "DOI": doi}],
                    "error": None,
                }

            def add_to_collection(self, item_key, collection_key, *, library_id=1):
                return {"ok": True, "data": "added", "error": None}

            def manage_tags(self, item_key, add_tags, remove_tags, *, library_id=1):
                return {"ok": True, "data": "tagged", "error": None}

            def import_from_doi(self, *args, **kwargs):
                raise AssertionError("translator should not run when dedupe hits")

        payload = imports_mod.import_doi(
            self.runtime,
            FakeBridge(),
            "10.1038/s41592-024-02201-0",
            collection_key="COLLAAAA",
            tags=["t1"],
            dedupe=True,
            if_exists="file",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "already_exists")
        self.assertEqual(payload["key"], "EXISTING1")
        self.assertEqual(payload["source"], "library-dedupe")
        self.assertTrue(payload.get("modified"))

    def test_result_helpers_and_doctor_shape(self):
        from cli_anything.zotero.core import doctor as doctor_mod
        from cli_anything.zotero.core.results import exit_code_for, result_payload

        ok_payload = result_payload(action="x", ok=True, status="success", code="OK")
        self.assertEqual(exit_code_for(ok_payload), 0)
        bad = result_payload(action="x", ok=False, status="error", code="E", error="nope")
        self.assertEqual(exit_code_for(bad), 1)
        partial = result_payload(action="x", ok=True, status="partial_success")
        self.assertEqual(exit_code_for(partial), 1)

        class FakeBridge:
            def bridge_endpoint_active(self):
                return False

            def execute_js_http_required(self, *args, **kwargs):
                return {"ok": False, "data": None, "error": "down"}

        payload = doctor_mod.run_doctor(self.runtime, FakeBridge())
        self.assertEqual(payload["action"], "app_doctor")
        self.assertIn("checks", payload)
        self.assertIn("next_steps", payload)
        self.assertIn("write_ready", payload)

    def test_pdf_source_and_arxiv_helpers(self):
        from cli_anything.zotero.core import add as add_mod
        from cli_anything.zotero.core import pdf_fetch

        self.assertEqual(pdf_fetch.parse_sources("zotero,arxiv"), ["zotero", "arxiv"])
        self.assertEqual(pdf_fetch.extract_arxiv_id("https://arxiv.org/abs/2602.02093v1"), "2602.02093")
        self.assertEqual(add_mod.normalize_arxiv_id("arXiv:2602.02093"), "2602.02093")
        self.assertTrue(pdf_fetch.arxiv_pdf_urls("10.48550/arXiv.2602.02093")[0].endswith("2602.02093.pdf"))
        with self.assertRaises(ValueError):
            pdf_fetch.parse_sources("scihub")

    def test_fetch_pdf_already_has_pdf_short_circuit(self):
        from cli_anything.zotero.core import pdf_fetch

        class FakeBridge:
            def find_pdf(self, *args, **kwargs):
                raise AssertionError("should not call find_pdf")

        with mock.patch(
            "cli_anything.zotero.utils.zotero_sqlite.resolve_item",
            return_value={"key": "ABCDEFGH", "title": "T", "DOI": "10.1/x", "hasPdf": True},
        ):
            payload = pdf_fetch.fetch_pdf_for_item(self.runtime, FakeBridge(), "ABCDEFGH")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["code"], "ALREADY_HAS_PDF")

    def test_fetch_pdf_asks_zotero_when_sqlite_cannot_see_the_item(self):
        """A just-imported item is still in Zotero's WAL, invisible to the SQLite snapshot."""
        from cli_anything.zotero.core import pdf_fetch

        class FakeBridge:
            def __init__(self, data):
                self.data = data

            def item_pdf_summary(self, key, *, library_id=1):
                return {"ok": True, "data": self.data}

            def find_pdf(self, *args, **kwargs):
                raise AssertionError("should not call find_pdf")

        with mock.patch("cli_anything.zotero.utils.zotero_sqlite.resolve_item", return_value=None):
            payload = pdf_fetch.fetch_pdf_for_item(
                self.runtime,
                FakeBridge({"key": "ABCDEFGH", "title": "T", "DOI": "10.1/x", "hasPdf": True}),
                "ABCDEFGH",
            )
            self.assertEqual((payload["code"], payload["DOI"]), ("ALREADY_HAS_PDF", "10.1/x"))
            missing = pdf_fetch.fetch_pdf_for_item(self.runtime, FakeBridge(None), "ABCDEFGH")
        self.assertEqual(missing["code"], "ITEM_NOT_FOUND")

    def test_add_doi_wraps_import(self):
        from cli_anything.zotero.core import add as add_mod

        class FakeBridge:
            pass

        with mock.patch(
            "cli_anything.zotero.core.imports.import_doi",
            return_value={
                "ok": True,
                "status": "success",
                "code": "IMPORTED",
                "key": "NEWITEM1",
                "title": "Paper",
                "DOI": "10.1/xyz",
                "source": "crossref-bibtex",
            },
        ):
            payload = add_mod.add_doi(self.runtime, FakeBridge(), "10.1/xyz", fetch_pdf=False)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "add_doi")
        self.assertEqual(payload["key"], "NEWITEM1")

    def test_csl_json_conversion(self):
        from cli_anything.zotero.core.csl import csl_item_to_connector, normalize_import_json_payload

        csl = {
            "type": "article-journal",
            "title": "Hello CSL",
            "DOI": "10.1/csl",
            "author": [{"family": "Doe", "given": "Jane"}],
            "issued": {"date-parts": [[2024, 1, 2]]},
            "container-title": "Nature Methods",
        }
        item = csl_item_to_connector(csl)
        self.assertEqual(item["itemType"], "journalArticle")
        self.assertEqual(item["DOI"], "10.1/csl")
        self.assertEqual(item["date"], "2024-1-2")
        items, fmt = normalize_import_json_payload([csl])
        self.assertEqual(fmt, "csl-json")
        self.assertEqual(items[0]["title"], "Hello CSL")

    def test_merge_dry_run_preview(self):
        from cli_anything.zotero.core import hygiene

        seen = {"code": ""}

        class FakeBridge:
            def execute_js(self, code, **kwargs):
                # dry-run uses preview JS, not trash JS
                seen["code"] = code
                return {
                    "ok": True,
                    "data": {
                        "ok": True,
                        "keep": {"key": "KEEPKEY1", "title": "Keep", "tags": ["a"], "attachments": [], "notes": [], "collections": []},
                        "others": [
                            {
                                "key": "OTHERKEY",
                                "title": "Other",
                                "tags": ["b"],
                                "nAttachments": 1,
                                "nNotes": 0,
                                "attachments": [{"key": "ATT1", "title": "pdf"}],
                                "notes": [],
                                "collections": [{"key": "C1", "name": "Col"}],
                            }
                        ],
                        "missing": [],
                        "will": {
                            "move_attachments": 1,
                            "move_notes": 0,
                            "add_tags": ["b"],
                            "add_collections": [{"key": "C1", "name": "Col"}],
                            "trash_items": ["OTHERKEY"],
                        },
                    },
                    "error": None,
                }

        payload = hygiene.merge_items(FakeBridge(), "KEEPKEY1", ["OTHERKEY"], dry_run=True)
        self.assertIn("summarize", seen["code"])
        self.assertNotIn("deleted = true", seen["code"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "dry_run")
        self.assertEqual(payload["summary"]["move_attachments"], 1)
        self.assertIn("b", payload["summary"]["add_tags"])

    def test_docx_cite_rejects_bad_mode(self):
        from cli_anything.zotero.core import docx_pipeline

        payload = docx_pipeline.cite_document(
            self.runtime,
            object(),
            "x.docx",
            "y.docx",
            mode="nope",
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "INVALID_MODE")

    def test_merge_preview_sqlite_fallback(self):
        from cli_anything.zotero.core import hygiene

        class DeadBridge:
            def execute_js(self, *args, **kwargs):
                return {"ok": False, "data": None, "error": "bridge down"}

        with mock.patch(
            "cli_anything.zotero.core.hygiene._sqlite_summarize_item",
            side_effect=lambda runtime, key, library_id=1: {
                "key": key,
                "title": f"T-{key}",
                "DOI": "10.1/x" if key == "KEEPKEY1" else "10.1/x",
                "date": "2024",
                "itemType": "journalArticle",
                "tags": ["a"] if key == "KEEPKEY1" else ["b"],
                "collections": [{"key": "C1", "name": "Col"}] if key != "KEEPKEY1" else [],
                "attachments": [{"key": "ATT1"}] if key != "KEEPKEY1" else [],
                "notes": [],
                "nAttachments": 1 if key != "KEEPKEY1" else 0,
                "nNotes": 0,
                "nTags": 1,
                "nCollections": 1 if key != "KEEPKEY1" else 0,
            },
        ):
            payload = hygiene.preview_merge(
                DeadBridge(),
                "KEEPKEY1",
                ["OTHERKEY"],
                runtime=self.runtime,
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload.get("preview_source"), "sqlite")
        self.assertEqual(payload["summary"]["move_attachments"], 1)
        self.assertIn("b", payload["summary"]["add_tags"])

    def test_audit_log_roundtrip(self):
        from cli_anything.zotero.core import audit as audit_mod
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"ZOTERO_CLI_AUDIT_DIR": tmp}):
                entry = audit_mod.log_event("add_doi", ok=True, status="success", code="IMPORTED", key="ABCDEFGH")
                self.assertEqual(entry["action"], "add_doi")
                self.assertTrue(Path(tmp, "audit.jsonl").is_file())
                rows = audit_mod.tail(5)
                self.assertEqual(rows[-1]["key"], "ABCDEFGH")

    def test_add_url_routes_doi(self):
        from cli_anything.zotero.core import add as add_mod

        with mock.patch(
            "cli_anything.zotero.core.add.add_doi",
            return_value={"ok": True, "action": "add_doi", "key": "D1", "status": "success"},
        ) as mocked:
            payload = add_mod.add_url(self.runtime, object(), "https://doi.org/10.1038/s41586-021-03819-2")
        mocked.assert_called_once()
        self.assertEqual(payload["action"], "add_url")
        self.assertEqual(payload["url_kind"], "doi")

    def test_import_file_splits_multi_entry_bibtex(self):
        bib_path = Path(self.tmpdir.name) / "multi.bib"
        bib_path.write_text(
            "@article{a, title={One},}\n@article{b, title={Two},}\n",
            encoding="utf-8",
        )
        calls = []

        def fake_import(port, content, *, session_id=None, content_type="text/plain", timeout=120):
            calls.append(content)
            title = "One" if "One" in content else "Two"
            return [{"key": f"K{len(calls)}", "title": title}]

        with mock.patch.object(self.runtime, "connector_available", True):
            with mock.patch(
                "cli_anything.zotero.utils.zotero_http.connector_import_text",
                side_effect=fake_import,
            ):
                with mock.patch("cli_anything.zotero.utils.zotero_http.connector_update_session"):
                    payload = imports_mod.import_file(
                        self.runtime,
                        bib_path,
                        collection_ref="C1",
                        tags=["split"],
                    )

        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload.get("split_bib"))
        self.assertEqual(payload["imported_count"], 2)
        self.assertEqual(len(calls), 2)


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


class DeleteSemanticsTests(unittest.TestCase):
    """`delete` must trash by default; erasing must take a separate, explicit path."""

    def _captured_js(self, method, *args, **kwargs):
        seen = {}

        def fake_execute(self, code, **kw):
            seen["code"] = code
            return {"ok": True, "data": "TRASHED: x", "error": None}

        with mock.patch.object(jsbridge.JSBridgeClient, "execute_js", fake_execute):
            getattr(jsbridge.JSBridgeClient(), method)(*args, **kwargs)
        return seen["code"]

    def test_delete_item_defaults_to_trash_not_erase(self):
        code = self._captured_js("delete_item", "ITEMKEY1")
        self.assertIn("item.deleted = true", code)
        self.assertIn("saveTx", code)
        self.assertNotIn("eraseTx", code)

    def test_delete_item_permanent_uses_erase(self):
        code = self._captured_js("delete_item", "ITEMKEY1", permanent=True)
        self.assertIn("eraseTx", code)
        self.assertNotIn("item.deleted = true", code)

    def test_restore_item_untrashes(self):
        code = self._captured_js("restore_item", "ITEMKEY1")
        self.assertIn("item.deleted = false", code)
        self.assertIn("saveTx", code)
        self.assertNotIn("eraseTx", code)

    def test_delete_collection_defaults_to_trash_not_erase(self):
        code = self._captured_js("delete_collection", "COLLKEY1")
        self.assertIn("col.deleted = true", code)
        self.assertNotIn("eraseTx", code)

    def test_delete_collection_permanent_uses_erase(self):
        code = self._captured_js("delete_collection", "COLLKEY1", permanent=True)
        self.assertIn("eraseTx", code)

    def test_delete_collection_permanent_honours_delete_items(self):
        keep = self._captured_js("delete_collection", "COLLKEY1", permanent=True)
        self.assertIn("deleteItems: false", keep)
        cascade = self._captured_js(
            "delete_collection", "COLLKEY1", permanent=True, delete_items=True
        )
        self.assertNotIn("deleteItems: false", cascade)

    def test_restore_collection_untrashes(self):
        code = self._captured_js("restore_collection", "COLLKEY1")
        self.assertIn("col.deleted = false", code)
        self.assertNotIn("eraseTx", code)

    def test_delete_collection_with_items_uses_zotero_cascade(self):
        """--delete-items must reach items in subcollections, as the Zotero UI does.

        Zotero.Collection.trash() trashes every subcollection; only the
        deleteItems save option makes it trash the items at every depth too.
        Trashing just col.getChildItems() leaves nested items in the library
        while their subcollections sit in the trash.
        """
        cascade = self._captured_js("delete_collection", "COLLKEY1", delete_items=True)
        self.assertIn("saveTx({ deleteItems: true })", cascade)
        self.assertNotIn("getChildItems", cascade)
        keep = self._captured_js("delete_collection", "COLLKEY1")
        self.assertIn("saveTx({ deleteItems: false })", keep)

    def test_delete_collection_checks_trash_state_before_changing_anything(self):
        code = self._captured_js("delete_collection", "COLLKEY1", delete_items=True)
        self.assertLess(code.index("ALREADY_TRASHED"), code.index("deleted = true"))
        self.assertLess(code.index("ALREADY_TRASHED"), code.index("saveTx"))

    def test_restore_collection_brings_back_subcollections(self):
        """Trashing a collection trashes its subcollections, so restore must undo both."""
        code = self._captured_js("restore_collection", "COLLKEY1")
        self.assertIn("getDescendents(false, 'collection', true)", code)
        self.assertIn("c.deleted = false", code)
        self.assertIn("executeTransaction", code)


class DestructiveGateTests(unittest.TestCase):
    """--confirm must never be sufficient to erase."""

    def setUp(self):
        from cli_anything.zotero import zotero_cli

        self.gate = zotero_cli.gate_destructive
        self.cli_mod = zotero_cli

    def _gate(self, **kwargs):
        params = {"noun": "item", "target": "K1", "permanent": False, "confirm": False, "yes_erase": False}
        params.update(kwargs)
        return self.gate(**params)

    def test_trash_with_confirm_proceeds(self):
        self.assertIsNone(self._gate(confirm=True))

    def test_trash_without_confirm_noninteractive_aborts(self):
        import click as click_mod

        with mock.patch.object(self.cli_mod, "_stdin_is_interactive", return_value=False):
            with self.assertRaises(click_mod.ClickException):
                self._gate()

    def test_permanent_with_confirm_alone_does_not_erase(self):
        """The core guard: a habitual --confirm must not authorize an erase."""
        import click as click_mod

        with mock.patch.object(self.cli_mod, "_stdin_is_interactive", return_value=False):
            with self.assertRaises(click_mod.ClickException) as ctx:
                self._gate(permanent=True, confirm=True)
        self.assertIn("--yes-erase", str(ctx.exception))

    def test_permanent_still_prompts_interactively_despite_confirm(self):
        import click as click_mod

        with mock.patch.object(self.cli_mod, "_stdin_is_interactive", return_value=True):
            with mock.patch.object(click_mod, "confirm", return_value=False) as prompt:
                with self.assertRaises(click_mod.ClickException):
                    self._gate(permanent=True, confirm=True)
        prompt.assert_called_once()

    def test_permanent_with_yes_erase_proceeds(self):
        self.assertIsNone(self._gate(permanent=True, yes_erase=True))


class DuplicateDetectionTests(unittest.TestCase):
    """Replacement for the private Zotero.Duplicates._findDuplicates() call."""

    def setUp(self):
        from cli_anything.zotero.core import duplicates

        self.dup = duplicates

    # ── normalization ──
    def test_normalize_title_folds_diacritics_latex_and_punctuation(self):
        self.assertEqual(
            self.dup.normalize_title(r"Ni\~{n}o's \emph{Health}: A Review!"),
            self.dup.normalize_title("Ninos Health  A  Review"),
        )
        self.assertEqual(
            self.dup.normalize_title("<i>Plasmodium</i> falciparum"),
            "plasmodium falciparum",
        )

    def test_normalize_doi_folds_every_resolver_form(self):
        expected = "10.1787/ae3016b9-en"
        for raw in (
            "10.1787/ae3016b9-en",
            "10.1787/AE3016B9-EN",
            "doi:10.1787/ae3016b9-en",
            "https://doi.org/10.1787/ae3016b9-en",
            "http://dx.doi.org/10.1787/ae3016b9-en",
            # doubled prefix: a single prefix-strip leaves this unequal to the bare DOI
            "https://doi.org/https://doi.org/10.1787/ae3016b9-en",
        ):
            self.assertEqual(self.dup.normalize_doi(raw), expected, raw)
        self.assertEqual(
            self.dup.normalize_doi("http://doi.acm.org/10.1145/585597.585602"),
            "10.1145/585597.585602",
        )

    # ── clustering ──
    @staticmethod
    def _rec(key, title="A sufficiently long title", doi="", year=2020, creators=("Smith",), **kw):
        rec = {
            "key": key,
            "itemType": "journalArticle",
            "title": title,
            "date": str(year),
            "year": year,
            "DOI": doi,
            "creators": list(creators),
            "numAttachments": 0,
            "numPdfAttachments": 0,
            "citationKey": None,
            "citationKeyPinned": False,
        }
        rec.update(kw)
        return rec

    def test_clusters_on_exact_doi(self):
        clusters = self.dup.cluster_records([
            self._rec("A", title="One thing", doi="10.1000/x"),
            self._rec("B", title="Totally different heading", doi="https://doi.org/10.1000/x"),
        ])
        self.assertEqual(len(clusters), 1)
        self.assertIn("doi", clusters[0]["match_reason"])

    def test_clusters_on_title_confirmed_by_creator_and_year(self):
        clusters = self.dup.cluster_records([
            self._rec("A", title="The performance of performance standards"),
            self._rec("B", title="The Performance of Performance Standards!"),
        ])
        self.assertEqual(len(clusters), 1)
        self.assertIn("title+creator+year", clusters[0]["match_reason"])

    def test_same_title_different_authors_is_not_a_duplicate(self):
        clusters = self.dup.cluster_records([
            self._rec("A", title="Introduction to the symposium", creators=("Smith",)),
            self._rec("B", title="Introduction to the symposium", creators=("Jones",)),
        ])
        self.assertEqual(clusters, [])

    def test_same_title_and_author_different_year_is_not_a_duplicate_at_tolerance_zero(self):
        records = [
            self._rec("A", title="Annual survey of practice", year=2018),
            self._rec("B", title="Annual survey of practice", year=2021),
        ]
        self.assertEqual(self.dup.cluster_records(records, year_tolerance=0), [])
        self.assertEqual(self.dup.cluster_records(records, year_tolerance=3)[0]["count"], 2)

    def test_missing_year_or_creator_never_clusters_on_title_alone(self):
        self.assertEqual(
            self.dup.cluster_records([
                self._rec("A", title="A shared long title", year=None, date=""),
                self._rec("B", title="A shared long title"),
            ]),
            [],
        )
        self.assertEqual(
            self.dup.cluster_records([
                self._rec("A", title="A shared long title", creators=()),
                self._rec("B", title="A shared long title"),
            ]),
            [],
        )

    def test_preprint_published_pair_clusters_at_default_tolerance(self):
        clusters = self.dup.cluster_records(
            [
                self._rec("A", title="Medical worker migration", doi="10.31235/osf.io/m79h2", year=2020),
                self._rec("B", title="Medical worker migration", doi="10.1162/rest_a_01131", year=2021),
            ],
            year_tolerance=1,
        )
        self.assertEqual(len(clusters), 1)

    def test_clusters_are_transitive_across_rules(self):
        clusters = self.dup.cluster_records([
            self._rec("A", title="Shared long title here", doi="10.1000/x"),
            self._rec("B", title="Shared long title here", doi=""),
            self._rec("C", title="Unrelated other title", doi="10.1000/x"),
        ])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 3)

    def test_pinned_citation_key_conflict_is_flagged(self):
        clusters = self.dup.cluster_records([
            self._rec("A", title="Shared long title here", citationKey="smith2020a", citationKeyPinned=True),
            self._rec("B", title="Shared long title here", citationKey="smith2020b", citationKeyPinned=True),
        ])
        self.assertTrue(clusters[0]["citation_key_conflict"])
        self.assertEqual(clusters[0]["pinned_citation_keys"], ["smith2020a", "smith2020b"])

    def test_keep_suggestion_prefers_pinned_key_then_pdf(self):
        clusters = self.dup.cluster_records([
            self._rec("A", title="Shared long title here"),
            self._rec("B", title="Shared long title here", citationKey="k", citationKeyPinned=True),
        ])
        self.assertEqual(clusters[0]["keep_suggestion"], "B")

    # ── failure semantics: never an empty success ──
    class _Bridge:
        def __init__(self, response):
            self.response = response

        def execute_js(self, code, **kwargs):
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

    def test_bridge_transport_failure_is_not_ok(self):
        payload = self.dup.find_duplicates(self._Bridge({"ok": False, "error": "plugin down"}))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "BRIDGE_ERROR")

    def test_bridge_exception_is_not_ok(self):
        payload = self.dup.find_duplicates(self._Bridge(RuntimeError("boom")))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "BRIDGE_ERROR")

    def test_malformed_payload_is_not_ok(self):
        payload = self.dup.find_duplicates(self._Bridge({"ok": True, "data": {"count": 0, "items": []}}))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "BRIDGE_BAD_PAYLOAD")

    def test_zero_scan_of_nonempty_library_is_not_ok(self):
        """The exact silent false negative the private API produced."""
        payload = self.dup.find_duplicates(
            self._Bridge({
                "ok": True,
                "data": {"ok": True, "scanned": 0, "libraryTopLevel": 2415,
                         "libraryAllItems": 4843, "bbt": True, "items": []},
            })
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "EMPTY_SCAN")

    def test_genuinely_clean_library_is_a_distinguishable_success(self):
        payload = self.dup.find_duplicates(
            self._Bridge({
                "ok": True,
                "data": {"ok": True, "scanned": 2, "libraryTopLevel": 2,
                         "libraryAllItems": 5, "bbt": False,
                         "items": [self._rec("A", title="One title here"),
                                   self._rec("B", title="Another title there", creators=("Jones",))]},
            })
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["group_count"], 0)
        self.assertEqual(payload["scanned"], 2)

    def test_fetch_js_uses_no_private_api(self):
        js = self.dup.fetch_items_js(1)
        self.assertNotIn("_findDuplicates", js)
        self.assertNotIn("Zotero.Duplicates", js)
        self.assertIn("Zotero.Items.getAll", js)

    def test_legacy_output_fields_are_preserved(self):
        payload = self.dup.find_duplicates(
            self._Bridge({
                "ok": True,
                "data": {"ok": True, "scanned": 2, "libraryTopLevel": 2,
                         "libraryAllItems": 2, "bbt": True,
                         "items": [self._rec("A", title="Shared long title here"),
                                   self._rec("B", title="Shared long title here")]},
            })
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["items"]), 2)
        for entry in payload["items"]:
            for legacy_field in ("key", "title", "date", "setID"):
                self.assertIn(legacy_field, entry)


class BatchParsingTests(unittest.TestCase):
    """`batch` validates every line before anything runs (#7)."""

    def setUp(self):
        from cli_anything.zotero.core import batch

        self.batch = batch

    def _parse(self, lines, **kw):
        return self.batch.parse_lines(lines, **kw)

    def test_parses_every_op_and_skips_blanks_and_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "n.md"
            note.write_text("# Heading\n\nbody", encoding="utf-8")
            ops, errors = self._parse([
                "# comment",
                "",
                '{"op": "tag", "key": "ABCD1234", "add": "reviewed"}',
                '{"op": "tag", "keys": ["ABCD1234", "EFGH5678"], "remove": ["unread"]}',
                '{"op": "update", "item_key": "ABCD1234", "fields": {"title": "T", "volume": 3}}',
                '{"op": "note", "key": "ABCD1234", "file": "n.md"}',
                '{"op": "note", "key": "ABCD1234", "text": "plain"}',
                '{"op": "add-doi", "doi": "10.1000/xyz", "collection": "COLL0001", "tags": "t", "fetch_pdf": true}',
                '{"op": "add-pmid", "pmid": 12345678}',
            ], base_dir=Path(tmp))
        self.assertEqual(errors, [])
        self.assertEqual([o["op"] for o in ops], ["tag", "tag", "update", "note", "note", "add-doi", "add-pmid"])
        self.assertEqual([o["line"] for o in ops], [3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(ops[0]["keys"], ["ABCD1234"])
        self.assertEqual(ops[0]["add"], ["reviewed"])
        self.assertEqual(ops[2]["key"], "ABCD1234")  # item_key alias
        self.assertEqual(ops[2]["fields"], {"title": "T", "volume": "3"})
        self.assertEqual(ops[3]["format"], "markdown")  # inferred from .md
        self.assertTrue(ops[3]["file"].endswith("n.md"))
        self.assertEqual(ops[4]["format"], "text")
        self.assertEqual(ops[5]["tags"], ["t"])
        self.assertTrue(ops[5]["fetch_pdf"])
        self.assertEqual(ops[6]["pmid"], "12345678")

    def test_default_op_applies_to_lines_without_op(self):
        ops, errors = self._parse(['{"key": "ABCD1234", "add": ["x"]}'], default_op="tag")
        self.assertEqual(errors, [])
        self.assertEqual(ops[0]["op"], "tag")

    def test_invalid_lines_are_reported_with_line_numbers(self):
        ops, errors = self._parse([
            "{not json",
            "[1, 2]",
            '{"op": "explode", "key": "ABCD1234"}',
            '{"op": "tag", "key": "bad"}',
            '{"op": "tag", "key": "ABCD1234"}',
            '{"op": "note", "key": "ABCD1234", "text": "a", "file": "b.md"}',
            '{"op": "note", "key": "ABCD1234", "file": "missing.md"}',
            '{"op": "add-doi", "doi": "10.1/x", "if_exists": "merge"}',
            '{"op": "add-pmid", "pmid": "PMC123"}',
            '{"op": "update", "key": "ABCD1234", "fields": {}}',
        ], base_dir=Path(tempfile.gettempdir()))
        self.assertEqual(ops, [])
        self.assertEqual([e["line"] for e in errors], list(range(1, 11)))

    def test_parse_keys_validates_and_dedupes(self):
        self.assertEqual(
            self.batch.parse_keys(["ABCD1234", "  EFGH5678  # note", "", "# skip", "ABCD1234"]),
            ["ABCD1234", "EFGH5678"],
        )
        with self.assertRaises(self.batch.BatchLineError):
            self.batch.parse_keys(["ABCD1234", "'); evil(); ('"])

    def test_invalid_input_summary_runs_nothing(self):
        payload = self.batch.summarize([], parse_errors=[{"line": 2, "error": "x"}])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "INVALID_INPUT")


class _FakeBatchBridge:
    """Records calls; items in `missing` do not exist."""

    def __init__(self, missing=()):
        self.missing = set(missing)
        self.calls = []

    def manage_tags_many(self, entries, *, library_id=1):
        self.calls.append(("tags", [e["key"] for e in entries], library_id))
        return {"ok": True, "data": [
            {"key": e["key"], "ok": False, "error": "item not found"} if e["key"] in self.missing
            else {"key": e["key"], "ok": True, "changed": True, "title": "T"}
            for e in entries
        ]}

    def update_item_fields(self, key, fields, *, library_id=1):
        self.calls.append(("update", key))
        if key in self.missing:
            return {"ok": True, "data": f"ERROR: item {key} not found"}
        return {"ok": True, "data": "OK: updated T"}

    def add_child_note(self, key, html, *, library_id=1):
        self.calls.append(("note", key, html))
        return {"ok": True, "data": {"ok": True, "key": "NOTE0001", "title": "T"}}


class BatchRunTests(unittest.TestCase):
    def setUp(self):
        from cli_anything.zotero.core import batch

        self.batch = batch

    def _ops(self, *lines):
        ops, errors = self.batch.parse_lines(lines)
        self.assertEqual(errors, [])
        return ops

    def test_consecutive_tag_lines_share_one_bridge_call(self):
        bridge = _FakeBatchBridge()
        ops = self._ops(
            '{"op": "tag", "key": "AAAA1111", "add": "x"}',
            '{"op": "tag", "keys": ["BBBB2222", "CCCC3333"], "add": "x"}',
            '{"op": "update", "key": "AAAA1111", "fields": {"title": "T"}}',
            '{"op": "tag", "key": "DDDD4444", "add": "x"}',
        )
        rows = self.batch.run_batch(ops, runtime=None, bridge=bridge, library_id=7)
        self.assertEqual(
            bridge.calls,
            [("tags", ["AAAA1111", "BBBB2222", "CCCC3333"], 7), ("update", "AAAA1111"), ("tags", ["DDDD4444"], 7)],
        )
        self.assertEqual([r["line"] for r in rows], [1, 2, 2, 3, 4])
        self.assertTrue(all(r["ok"] for r in rows))
        self.assertEqual(self.batch.summarize(rows)["status"], "success")

    def test_large_tag_runs_are_chunked(self):
        bridge = _FakeBatchBridge()
        keys = [f"K{i:07d}" for i in range(self.batch.TAG_CHUNK + 5)]
        payload = self.batch.tag_items(bridge, keys, ["x"], [], library_id=1)
        self.assertEqual([len(c[1]) for c in bridge.calls], [self.batch.TAG_CHUNK, 5])
        self.assertEqual(payload["total"], len(keys))
        self.assertTrue(payload["ok"])

    def test_bridge_error_strings_count_as_failures(self):
        bridge = _FakeBatchBridge(missing={"MISS0000"})
        rows = self.batch.run_batch(
            self._ops(
                '{"op": "update", "key": "MISS0000", "fields": {"title": "T"}}',
                '{"op": "tag", "keys": ["AAAA1111", "MISS0000"], "add": "x"}',
            ),
            runtime=None,
            bridge=bridge,
        )
        self.assertEqual([r["ok"] for r in rows], [False, True, False])
        payload = self.batch.summarize(rows)
        self.assertEqual((payload["status"], payload["failed"], payload["succeeded"]), ("partial_success", 2, 1))
        from cli_anything.zotero.core.results import exit_code_for

        self.assertEqual(exit_code_for(payload), 1)

    def test_stop_on_error_skips_the_rest(self):
        bridge = _FakeBatchBridge(missing={"MISS0000"})
        rows = self.batch.run_batch(
            self._ops(
                '{"op": "update", "key": "MISS0000", "fields": {"title": "T"}}',
                '{"op": "update", "key": "AAAA1111", "fields": {"title": "T"}}',
            ),
            runtime=None,
            bridge=bridge,
            stop_on_error=True,
        )
        self.assertEqual(bridge.calls, [("update", "MISS0000")])
        self.assertEqual(rows[1]["status"], "skipped")
        self.assertEqual(self.batch.summarize(rows)["skipped"], 1)

    def test_exception_in_one_op_does_not_abort_the_batch(self):
        bridge = _FakeBatchBridge()

        def boom(*a, **k):
            raise RuntimeError("bridge down")

        bridge.update_item_fields = boom
        rows = self.batch.run_batch(
            self._ops(
                '{"op": "update", "key": "AAAA1111", "fields": {"title": "T"}}',
                '{"op": "note", "key": "AAAA1111", "text": "hello"}',
            ),
            runtime=None,
            bridge=bridge,
        )
        self.assertFalse(rows[0]["ok"])
        self.assertIn("bridge down", rows[0]["error"])
        self.assertTrue(rows[1]["ok"])
        self.assertEqual(rows[1]["note_key"], "NOTE0001")

    def test_markdown_note_file_is_converted_to_html(self):
        bridge = _FakeBatchBridge()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "n.md").write_text("**bold** text", encoding="utf-8")
            ops, _ = self.batch.parse_lines(['{"op": "note", "key": "AAAA1111", "file": "n.md"}'], base_dir=Path(tmp))
            self.batch.run_batch(ops, runtime=None, bridge=bridge)
        html = bridge.calls[0][2]
        self.assertIn("<strong>bold</strong>", html)


class BatchBridgeJsTests(unittest.TestCase):
    """New bridge calls embed values as JSON literals, not hand-escaped strings."""

    def _captured(self, method, *args, **kwargs):
        seen = {}

        def fake_execute(self, code, **kw):
            seen["code"] = code
            return {"ok": True, "data": [], "error": None}

        with mock.patch.object(jsbridge.JSBridgeClient, "execute_js", fake_execute):
            getattr(jsbridge.JSBridgeClient(), method)(*args, **kwargs)
        return seen["code"]

    def test_manage_tags_many_embeds_entries_as_json(self):
        tricky = "it's a \\ \"tag\"\nnext"
        code = self._captured("manage_tags_many", [{"key": "AAAA1111", "add": [tricky], "remove": []}], library_id=3)
        self.assertIn(json.dumps(tricky, ensure_ascii=False), code)
        self.assertIn("var LIB = 3;", code)
        self.assertIn("hasChanged()", code)

    def test_update_and_single_tag_survive_backslashes_and_newlines(self):
        value = "C:\\path\nline 2 'quoted'"
        code = self._captured("update_item_fields", "AAAA1111", {"extra": value})
        self.assertIn(json.dumps(value, ensure_ascii=False), code)
        code = self._captured("manage_tags", "AAAA1111", [value], [])
        self.assertIn(f"item.addTag({json.dumps(value, ensure_ascii=False)})", code)

    def test_add_child_note_checks_parent_is_regular_item(self):
        code = self._captured("add_child_note", "AAAA1111", "<p>x</p>")
        self.assertIn("isRegularItem()", code)
        self.assertIn('note.setNote("<p>x</p>")', code)


class BatchCliTests(unittest.TestCase):
    def _run(self, argv, bridge):
        import contextlib
        import io

        from cli_anything.zotero import zotero_cli

        out = io.StringIO()
        with mock.patch.object(zotero_cli, "current_bridge", return_value=bridge), \
                mock.patch.object(zotero_cli, "current_runtime", return_value=None), \
                mock.patch.object(zotero_cli, "current_session", return_value={}), \
                contextlib.redirect_stdout(out):
            code = zotero_cli.dispatch(argv)
        return code, out.getvalue()

    def test_batch_with_an_invalid_line_changes_nothing(self):
        bridge = _FakeBatchBridge()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ops.jsonl"
            path.write_text('{"op": "tag", "key": "AAAA1111", "add": "x"}\n{"op": "tag", "key": "nope"}\n', encoding="utf-8")
            code, out = self._run(["--json", "batch", str(path)], bridge)
        self.assertEqual(code, 1)
        self.assertEqual(bridge.calls, [])
        self.assertEqual(json.loads(out)["code"], "INVALID_INPUT")

    def test_batch_dry_run_calls_nothing(self):
        bridge = _FakeBatchBridge()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ops.jsonl"
            path.write_text('{"key": "AAAA1111", "add": "x"}\n', encoding="utf-8")
            code, out = self._run(["--json", "batch", str(path), "--op", "tag", "--dry-run"], bridge)
        self.assertEqual(code, 0)
        self.assertEqual(bridge.calls, [])
        self.assertEqual(json.loads(out)["total"], 1)

    def test_batch_runs_and_reports(self):
        bridge = _FakeBatchBridge(missing={"MISS0000"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ops.jsonl"
            path.write_text('{"op": "tag", "keys": ["AAAA1111", "MISS0000"], "add": "x"}\n', encoding="utf-8")
            code, out = self._run(["--json", "batch", str(path)], bridge)
        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual((payload["succeeded"], payload["failed"]), (1, 1))

    def test_item_tag_accepts_many_keys_and_keys_from_file(self):
        bridge = _FakeBatchBridge()
        with tempfile.TemporaryDirectory() as tmp:
            keys = Path(tmp) / "keys.txt"
            keys.write_text("BBBB2222\nCCCC3333\nAAAA1111\n", encoding="utf-8")
            code, out = self._run(
                ["--json", "item", "tag", "AAAA1111", "--keys-from", str(keys), "--add", "reviewed"], bridge
            )
        self.assertEqual(code, 0)
        self.assertEqual(bridge.calls, [("tags", ["AAAA1111", "BBBB2222", "CCCC3333"], 1)])
        self.assertEqual(json.loads(out)["total"], 3)

    def test_item_tag_single_key_keeps_the_old_path(self):
        bridge = mock.Mock()
        bridge.manage_tags.return_value = {"ok": True, "data": "OK: tags updated for T"}
        code, out = self._run(["item", "tag", "AAAA1111", "--add", "x"], bridge)
        self.assertEqual(code, 0)
        bridge.manage_tags.assert_called_once()
        self.assertIn("OK: tags updated", out)
