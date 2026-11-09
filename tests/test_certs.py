"""The HTTPS certificate helper (hasc.certs), with a temporary folder and made-up addresses."""
from pathlib import Path
import datetime
import tempfile
import unittest
from unittest.mock import patch

from cryptography import x509

from hasc import certs


def _load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


class TestCerts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp_dir.name) / "config"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_certs_first_run_makes_authority_and_server_certificate(self):
        result = certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        self.assertEqual(result, {"ca_created": True, "server_created": True})
        for name in ("ca.crt", "ca.key", "server.crt", "server.key"):
            self.assertTrue((self.folder / name).is_file(), name)

    def test_certs_server_certificate_chains_to_the_authority(self):
        certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        ca, server = _load(self.folder / "ca.crt"), _load(self.folder / "server.crt")
        server.verify_directly_issued_by(ca)
        self.assertTrue(ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertFalse(server.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)

    def test_certs_names_and_addresses_covered(self):
        certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10", "192.168.2.15"])
        san = _load(self.folder / "server.crt").extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        self.assertEqual(set(san.get_values_for_type(x509.DNSName)), {"localhost", "office-pc"})
        self.assertEqual(
            {str(ip) for ip in san.get_values_for_type(x509.IPAddress)},
            {"127.0.0.1", "192.168.1.10", "192.168.2.15"},
        )

    def test_certs_second_run_keeps_everything(self):
        certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        before = (self.folder / "ca.crt").read_bytes(), (self.folder / "server.crt").read_bytes()
        result = certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        self.assertEqual(result, {"ca_created": False, "server_created": False})
        after = (self.folder / "ca.crt").read_bytes(), (self.folder / "server.crt").read_bytes()
        self.assertEqual(before, after)

    def test_certs_address_change_renews_server_certificate_and_keeps_authority(self):
        certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        ca_before = (self.folder / "ca.crt").read_bytes()
        result = certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.2.15"])
        self.assertEqual(result, {"ca_created": False, "server_created": True})
        self.assertEqual((self.folder / "ca.crt").read_bytes(), ca_before)

    def test_certs_server_certificate_renewed_before_it_runs_out(self):
        certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=certs.SERVER_DAYS - 30)
        with patch.object(certs, "_now", return_value=later):
            result = certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        self.assertTrue(result["server_created"])

    def test_certs_server_validity_within_apple_limit(self):
        certs.ensure_certificates(self.folder, "OFFICE-PC", [])
        server = _load(self.folder / "server.crt")
        self.assertLessEqual((server.not_valid_after_utc - server.not_valid_before_utc).days, 825)

    def test_certs_half_missing_authority_is_not_replaced(self):
        certs.ensure_certificates(self.folder, "OFFICE-PC", [])
        (self.folder / "ca.key").unlink()
        with self.assertRaises(ValueError):
            certs.ensure_certificates(self.folder, "OFFICE-PC", [])
        self.assertFalse((self.folder / "ca.key").exists())

    def test_certs_mismatched_server_key_is_renewed(self):
        certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        other = Path(self.temp_dir.name) / "other"
        certs.ensure_certificates(other, "OFFICE-PC", ["192.168.1.10"])
        (self.folder / "server.key").write_bytes((other / "server.key").read_bytes())
        result = certs.ensure_certificates(self.folder, "OFFICE-PC", ["192.168.1.10"])
        self.assertTrue(result["server_created"])

    def test_certs_cli_rejects_a_bad_address(self):
        self.assertEqual(certs.main(["--dir", str(self.folder), "--ip", "not-an-ip"]), 1)


if __name__ == "__main__":
    unittest.main()
