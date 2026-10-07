"""Unit tests for chrome secrets parsers and owner-action detect (no live Magisk)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path


def _make_cookies_db() -> bytes:
    path = Path(tempfile.mkstemp(suffix=".db")[1])
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE cookies ("
        "creation_utc INTEGER, host_key TEXT, top_frame_site_key TEXT, name TEXT, "
        "value TEXT, encrypted_value BLOB, path TEXT, expires_utc INTEGER, "
        "is_secure INTEGER, is_httponly INTEGER, last_access_utc INTEGER, "
        "has_expires INTEGER, is_persistent INTEGER, priority INTEGER, "
        "samesite INTEGER, source_scheme INTEGER, source_port INTEGER, "
        "last_update_utc INTEGER, source_type INTEGER, has_cross_site_ancestor INTEGER)"
    )
    con.execute(
        "INSERT INTO cookies VALUES (1,'.example.com','','sid','',?, '/',0,1,1,9,1,1,1,0,2,443,9,0,0)",
        (b"v10" + b"\x00" * 20,),
    )
    con.execute(
        "INSERT INTO cookies VALUES (1,'.google.com','','NID','',?, '/',0,1,1,8,1,1,1,0,2,443,8,0,0)",
        (b"v10" + b"\x01" * 20,),
    )
    con.commit()
    con.close()
    raw = path.read_bytes()
    path.unlink(missing_ok=True)
    return raw


def _make_webdata_db() -> bytes:
    path = Path(tempfile.mkstemp(suffix=".db")[1])
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE autofill (name TEXT, value TEXT, value_lower TEXT, "
        "date_created INTEGER, date_last_used INTEGER, count INTEGER)"
    )
    con.execute(
        "INSERT INTO autofill VALUES ('email','a@b.de','a@b.de',1,2,5)"
    )
    con.execute(
        "CREATE TABLE address_type_tokens (guid TEXT, type INTEGER, value TEXT, "
        "verification_status INTEGER, observations BLOB)"
    )
    con.execute(
        "INSERT INTO address_type_tokens VALUES ('g1',3,'Ada',1,X'')"
    )
    con.execute(
        "INSERT INTO address_type_tokens VALUES ('g1',5,'Lovelace',1,X'')"
    )
    con.commit()
    con.close()
    raw = path.read_bytes()
    path.unlink(missing_ok=True)
    return raw


class TestChromeSecretsParse(unittest.TestCase):
    def test_parse_cookies(self):
        from chrome_secrets import parse_cookies_db

        r = parse_cookies_db(_make_cookies_db(), limit=10)
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["encrypted_in_page"], 2)
        self.assertTrue(any(c["host"] == ".example.com" for c in r["cookies"]))

    def test_parse_autofill(self):
        from chrome_secrets import parse_web_data

        r = parse_web_data(_make_webdata_db())
        self.assertTrue(r["ok"])
        self.assertEqual(r["autofill"][0]["value"], "a@b.de")
        self.assertTrue(r["addresses"])


class TestChromeSecretsDetect(unittest.TestCase):
    def test_detect(self):
        from owner_action import detect_owner_action

        a = detect_owner_action("chrome secrets")
        self.assertIsNotNone(a)
        self.assertEqual(a.kind, "chrome_secrets")
        a2 = detect_owner_action("chrome cookies")
        self.assertEqual(a2.kind, "chrome_secrets")
        self.assertEqual(a2.params.get("section"), "cookies")
        a3 = detect_owner_action("apps list whatsapp")
        self.assertEqual(a3.kind, "apps_list")
        self.assertIn("whatsapp", a3.params.get("query", ""))
        a4 = detect_owner_action("tippe Anmelden")
        self.assertEqual(a4.kind, "android_input")
        a5 = detect_owner_action("öffne app de.number26.android")
        self.assertEqual(a5.kind, "app_open")
        a6 = detect_owner_action("chrome decrypt")
        self.assertEqual(a6.kind, "chrome_decrypt")
        self.assertTrue(a6.params.get("reveal"))
        a7 = detect_owner_action("chrome decrypt mask")
        self.assertEqual(a7.kind, "chrome_decrypt")
        self.assertFalse(a7.params.get("reveal"))


class TestLiveDecryptHelpers(unittest.TestCase):
    def test_guess_host_and_mask(self):
        from chrome_secrets import _guess_host_for_name, _mask_value, format_live_decrypt_report

        self.assertEqual(_guess_host_for_name("SID"), ".google.com")
        self.assertEqual(_guess_host_for_name("access_token"), "(oauth-token)")
        self.assertIn("…", _mask_value("abcdefghijklmnop", reveal=False))
        text = format_live_decrypt_report(
            {
                "ok": True,
                "items": [
                    {
                        "host": ".google.com",
                        "name": "SID",
                        "value": "secretvalue123",
                        "value_len": 14,
                        "reveal": True,
                    }
                ],
                "meta": {"pid": 1, "regions": 2, "method": "process_memory"},
                "note": "test",
            },
            reveal=True,
        )
        self.assertIn("SID=secretvalue123", text)

    def test_cookie_jar_write(self):
        from chrome_secrets import items_to_cookie_jar, write_cookie_jar
        from pathlib import Path
        from unittest import mock

        items = [
            {"host": ".google.com", "name": "SID", "value": "abc123session"},
            {"host": "(oauth-token)", "name": "access_token", "value": "tok"},
            {"host": ".google.com", "name": "HSID", "value": "hsidval99"},
        ]
        entries = items_to_cookie_jar(items)
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e["name"] != "access_token" for e in entries))
        with mock.patch("chrome_secrets.is_owner_equivalent_mode", return_value=True):
            written = write_cookie_jar(entries, basename="test_cookies")
        self.assertTrue(written["ok"])
        p = Path(written["netscape_path"])
        self.assertTrue(p.exists())
        body = p.read_text(encoding="utf-8")
        self.assertIn("SID", body)
        self.assertIn("Netscape", body)
        # cleanup
        for key in ("netscape_path", "json_path", "header_path"):
            Path(written[key]).unlink(missing_ok=True)

    def test_detect_ui_passwords_and_jar(self):
        from owner_action import detect_owner_action

        a = detect_owner_action("ui passwords")
        self.assertEqual(a.kind, "ui_passwords")
        a2 = detect_owner_action("passwortfelder")
        self.assertEqual(a2.kind, "ui_passwords")
        a3 = detect_owner_action("cookie jar")
        self.assertEqual(a3.kind, "cookie_jar_export")
        a4 = detect_owner_action("export cookies")
        self.assertEqual(a4.kind, "cookie_jar_export")


class TestUiPasswordExtract(unittest.TestCase):
    def test_extract_password_fields(self):
        from android_apps import extract_password_fields
        from ui_automation import UINode

        nodes = [
            UINode(
                text="user@example.com",
                content_desc="",
                resource_id="email",
                class_name="android.widget.EditText",
                clickable=True,
                enabled=True,
                is_password=False,
                bounds=(10, 10, 100, 50),
            ),
            UINode(
                text="Secret123!",
                content_desc="Passwort",
                resource_id="password",
                class_name="android.widget.EditText",
                clickable=True,
                enabled=True,
                is_password=True,
                bounds=(10, 60, 100, 100),
            ),
        ]
        fields = extract_password_fields(nodes, activity="demo")
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["password_text"], "Secret123!")
        self.assertEqual(fields[0]["username"], "user@example.com")


class TestAndroidAppsSearch(unittest.TestCase):
    def test_search(self):
        from android_apps import search_packages

        pkgs = ["com.whatsapp", "com.android.chrome", "de.number26.android"]
        hits = search_packages(pkgs, "whatsapp")
        self.assertEqual(hits[0], "com.whatsapp")
        hits2 = search_packages(pkgs, "n26")
        self.assertIn("de.number26.android", hits2)


if __name__ == "__main__":
    unittest.main()
