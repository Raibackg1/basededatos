"""Pruebas del auditor SQLite de solo lectura."""
import hashlib
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from detector.audit_report import render_audit_html, write_audit_files
from detector.database_audit import (
    inspect_schema,
    list_user_tables,
    map_semantics,
    open_readonly_db,
    profile_schema,
    quote_identifier,
    run_database_audit,
)


def create_fixture_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE parent(token_id TEXT PRIMARY KEY, created_at TEXT);
        CREATE TABLE events(
            id INTEGER PRIMARY KEY,
            token_id TEXT REFERENCES parent(token_id),
            value TEXT
        );
        CREATE INDEX idx_events_token ON events(token_id);
        INSERT INTO parent VALUES ('mint-1', '2026-01-01T00:00:00Z');
        INSERT INTO events VALUES (1, 'mint-1', 'alpha');
        INSERT INTO events VALUES (2, 'mint-1', NULL);
        INSERT INTO events VALUES (3, 'mint-1', 'gamma');
        INSERT INTO events VALUES (4, 'mint-1', 'delta');
        CREATE TABLE duplicate_rows(value TEXT);
        INSERT INTO duplicate_rows VALUES ('same'), ('same'), ('other');
        CREATE VIEW event_view AS SELECT id, token_id, value FROM events;
        CREATE TABLE "odd"" table" ("select" TEXT);
    """)
    conn.close()


class DatabaseAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = pathlib.Path(self.temp_dir.name)
        self.db_path = self.root / "source.db"
        create_fixture_db(self.db_path)
        self.out_dir = self.root / "out"

    def test_readonly_connection_rejects_write_and_preserves_source(self):
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        conn = open_readonly_db(self.db_path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE forbidden(x)")
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before)

    def test_quote_identifier_escapes_quotes(self):
        self.assertEqual(quote_identifier('odd" name'), '"odd"" name"')
        self.assertEqual(quote_identifier("select"), '"select"')

    def test_inspect_schema_lists_columns_indexes_foreign_keys_and_views(self):
        conn = open_readonly_db(self.db_path)
        self.addCleanup(conn.close)
        inventory = inspect_schema(conn)
        self.assertIn("events", inventory["tables"])
        self.assertIn("event_view", inventory["views"])
        self.assertIn('odd" table', inventory["tables"])
        events = inventory["tables"]["events"]
        self.assertEqual([column["name"] for column in events["columns"]], ["id", "token_id", "value"])
        self.assertTrue(any(index["name"] == "idx_events_token" for index in events["indexes"]))
        self.assertEqual(events["foreign_keys"][0]["table"], "parent")
        parent = inventory["tables"]["parent"]
        self.assertEqual(parent["columns"][0]["pk_position"], 1)

    def test_basic_profile_is_labeled_as_sample(self):
        conn = open_readonly_db(self.db_path)
        self.addCleanup(conn.close)
        inventory = inspect_schema(conn)
        result = profile_schema(conn, inventory, depth="basic", sample_rows=3)
        metric = result["tables"]["events"]["columns"]["value"]["null_count"]
        self.assertEqual(metric["scope"], "sample")
        self.assertEqual(metric["n_examined"], 3)
        self.assertEqual(metric["method"], "first_rows_preview")
        self.assertEqual(metric["value"], 1)

    def test_basic_profile_uses_actual_rows_when_table_is_smaller(self):
        conn = open_readonly_db(self.db_path)
        self.addCleanup(conn.close)
        inventory = inspect_schema(conn)
        result = profile_schema(conn, inventory, depth="basic", sample_rows=100)
        metric = result["tables"]["parent"]["columns"]["token_id"]["null_count"]
        self.assertEqual(metric["n_examined"], 1)

    def test_duplicate_counts_respect_sample_and_full_scan_scopes(self):
        basic = run_database_audit(str(self.db_path), sample_rows=2)
        basic_metric = basic["profiles"]["tables"]["duplicate_rows"]["duplicate_rows"]
        self.assertEqual(basic_metric["scope"], "sample")
        self.assertEqual(basic_metric["n_examined"], 2)
        self.assertEqual(basic_metric["value"], 1)

        deep = run_database_audit(str(self.db_path), depth="deep", deep_confirmed=True)
        deep_metric = deep["profiles"]["tables"]["duplicate_rows"]["duplicate_rows"]
        self.assertEqual(deep_metric["scope"], "full_scan")
        self.assertEqual(deep_metric["n_examined"], 3)
        self.assertEqual(deep_metric["value"], 1)

    def test_deep_profile_requires_confirmation_before_full_scan(self):
        with self.assertRaisesRegex(ValueError, "confirm"):
            run_database_audit(str(self.db_path), depth="deep", deep_confirmed=False)

    def test_deep_profile_reports_exact_full_scan_counts_after_confirmation(self):
        report = run_database_audit(str(self.db_path), depth="deep", deep_confirmed=True)
        metric = report["profiles"]["tables"]["events"]["columns"]["value"]["null_count"]
        self.assertEqual(metric["scope"], "full_scan")
        self.assertEqual(metric["n_examined"], 4)
        self.assertEqual(metric["value"], 1)
        self.assertEqual(report["profiles"]["tables"]["events"]["row_count"]["value"], 4)

    def test_table_preflight_lists_only_user_tables(self):
        self.assertEqual(list_user_tables(self.db_path), ["duplicate_rows", "events", 'odd" table', "parent"])

    def test_empty_database_is_rejected(self):
        empty = self.root / "empty.db"
        sqlite3.connect(empty).close()
        with self.assertRaisesRegex(ValueError, "schema|tabla|table"):
            run_database_audit(str(empty))

    def test_non_sqlite_file_is_rejected(self):
        invalid = self.root / "not-a-database.db"
        invalid.write_text("not sqlite", encoding="utf-8")
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            run_database_audit(str(invalid))

    def test_mapping_file_root_must_be_an_object(self):
        mapping_path = self.root / "mapping.json"
        mapping_path.write_text("null", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            run_database_audit(str(self.db_path), mapping_path=str(mapping_path))

    def test_mapping_reports_competing_aliases(self):
        inventory = {
            "tables": {
                "swaps": {
                    "columns": [
                        {"name": "token_id", "type": "TEXT"},
                        {"name": "mint", "type": "TEXT"},
                        {"name": "wallet", "type": "TEXT"},
                        {"name": "side", "type": "TEXT"},
                        {"name": "timestamp", "type": "TEXT"},
                    ]
                }
            },
            "views": {},
        }
        mapping = map_semantics(inventory)
        role = mapping["tables"]["trades"]["roles"]["token_id"]
        self.assertTrue(role["alternatives"])
        self.assertEqual(role["confidence_label"], "medium")
        self.assertEqual(mapping["tables"]["trades"]["confidence_label"], "medium")

    def test_mapping_reports_high_confidence_for_unique_exact_role(self):
        inventory = {
            "tables": {
                "tokens": {
                    "columns": [
                        {"name": "mint_address", "type": "TEXT"},
                        {"name": "token_name", "type": "TEXT"},
                        {"name": "created_at", "type": "TEXT"},
                    ]
                }
            },
            "views": {},
        }
        mapping = map_semantics(inventory)
        role = mapping["tables"]["tokens"]["roles"]["token_id"]
        self.assertEqual(role["column"], "mint_address")
        self.assertEqual(role["confidence_label"], "high")

    def test_mapping_distinguishes_unclassified_tables_from_missing_profiles(self):
        mapping = map_semantics({
            "tables": {"misc": {"columns": [{"name": "payload", "type": "TEXT"}]}},
            "views": {},
        })
        self.assertEqual(mapping["unclassified_tables"], ["misc"])
        self.assertIn("tokens", mapping["missing_profiles"])

    def test_duplicate_and_provenance_findings_are_structured(self):
        report = run_database_audit(str(self.db_path), sample_rows=2)
        duplicates = next(item for item in report["findings"] if item["id"] == "duplicates:duplicate_rows")
        self.assertEqual(duplicates["severity"], "warning")
        self.assertEqual(duplicates["scope"], "sample")
        provenance = next(item for item in report["findings"] if item["id"] == "provenance:unknown")
        self.assertEqual(provenance["severity"], "info")

    def test_mapping_override_is_validated(self):
        inventory = {
            "tables": {
                "tokens": {"columns": [{"name": "mint_address", "type": "TEXT"}]}
            },
            "views": {},
        }
        with self.assertRaisesRegex(ValueError, "missing|exist|column|columna"):
            map_semantics(inventory, {"tokens": {"table": "tokens", "columns": {"token_id": "not_here"}}})

    def test_mapping_override_is_explicit_and_manual(self):
        inventory = {
            "tables": {
                "tokens": {"columns": [
                    {"name": "mint_address", "type": "TEXT"},
                    {"name": "token_name", "type": "TEXT"},
                    {"name": "created_at", "type": "TEXT"},
                ]}
            },
            "views": {},
        }
        mapping = map_semantics(inventory, {
            "tokens": {"table": "tokens", "columns": {"token_id": "mint_address"}}
        })
        role = mapping["tables"]["tokens"]["roles"]["token_id"]
        self.assertEqual(role["source"], "manual")
        self.assertEqual(role["confidence_label"], "manual")

    def test_html_escapes_untrusted_schema_text(self):
        report = {
            "schema_version": 1,
            "generated_at_utc": "2026-10-13T00:00:00Z",
            "sqlite_version": sqlite3.sqlite_version,
            "source": {"filename": "source.db", "size_bytes": 1, "origin": "unknown"},
            "depth": "basic",
            "inventory": {
                "tables": {"<script>alert(1)</script>": {"columns": []}},
                "views": {},
            },
            "profiles": {"tables": {}},
            "semantic_mapping": {"tables": {}},
            "findings": [{
                "severity": "warning", "category": "test",
                "message": "<script>alert(2)</script>",
                "evidence": "<img src=x onerror=alert(2)>",
                "scope": "sample", "recommended_action": "<b>safe</b>",
            }],
            "warnings": [],
        }
        html = render_audit_html(report)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert(2)</script>", html)
        self.assertNotIn("<img src=x onerror=alert(2)>", html)

    def test_report_files_are_valid_and_versioned(self):
        report = run_database_audit(str(self.db_path), data_origin="synthetic")
        json_path, html_path = write_audit_files(report, str(self.out_dir))
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["schema_version"], 1)
        self.assertEqual(parsed["program_version"], "3.0.0")
        self.assertEqual(len(parsed["source"]["schema_sha256"]), 64)
        self.assertEqual(parsed["source"]["origin"], "synthetic")
        self.assertTrue(html_path.read_text(encoding="utf-8").startswith("<!doctype html>"))
        self.assertNotIn("alpha", json_path.read_text(encoding="utf-8"))

    def test_audit_only_emits_reports_and_does_not_run_trading(self):
        project_root = pathlib.Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "main.py", "--db", str(self.db_path), "--audit-only",
             "--out", str(self.out_dir)],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out_dir / "auditoria.json").exists())
        self.assertTrue((self.out_dir / "auditoria.html").exists())
        self.assertFalse((self.out_dir / "reporte.html").exists())

    def test_auditor_imports_without_scientific_libraries(self):
        project_root = pathlib.Path(__file__).resolve().parents[1]
        code = (
            "import sys; import detector.database_audit, detector.audit_report; "
            "blocked = {'numpy', 'pandas', 'scipy', 'sklearn', 'matplotlib'}; "
            "assert not any(name.split('.')[0] in blocked for name in sys.modules), sys.modules.keys()"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=project_root,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_demo_flag_cannot_regenerate_database_in_audit_mode(self):
        project_root = pathlib.Path(__file__).resolve().parents[1]
        demo_db = project_root / "demo_pumpfun.db"
        before = hashlib.sha256(demo_db.read_bytes()).hexdigest()
        result = subprocess.run(
            [sys.executable, "main.py", "--demo", "--audit-only", "--out", str(self.out_dir)],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(hashlib.sha256(demo_db.read_bytes()).hexdigest(), before)

    def test_deep_mode_noninteractive_requires_flag(self):
        project_root = pathlib.Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "main.py", "--db", str(self.db_path), "--audit-only",
             "--audit-depth", "deep", "--out", str(self.out_dir)],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("events", result.stdout)
        self.assertFalse((self.out_dir / "auditoria.json").exists())

    def test_confirmed_deep_cli_reports_full_scan(self):
        project_root = pathlib.Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "main.py", "--db", str(self.db_path), "--audit-only",
             "--audit-depth", "deep", "--confirm-deep", "--out", str(self.out_dir)],
            cwd=project_root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.out_dir / "auditoria.json").read_text(encoding="utf-8"))
        self.assertEqual(report["depth"], "deep")
        self.assertEqual(report["profiles"]["tables"]["events"]["row_count"]["scope"], "full_scan")

    def test_existing_demo_audits_as_synthetic_without_database_changes(self):
        project_root = pathlib.Path(__file__).resolve().parents[1]
        demo_db = project_root / "demo_pumpfun.db"
        before = hashlib.sha256(demo_db.read_bytes()).hexdigest()
        result = subprocess.run(
            [sys.executable, "main.py", "--db", str(demo_db), "--audit-only",
             "--data-origin", "synthetic", "--out", str(self.out_dir)],
            cwd=project_root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.out_dir / "auditoria.json").read_text(encoding="utf-8"))
        self.assertEqual(report["source"]["origin"], "synthetic")
        self.assertEqual(report["inventory"]["tables"]["trades"]["columns"][0]["name"], "token_mint")
        self.assertEqual(hashlib.sha256(demo_db.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
