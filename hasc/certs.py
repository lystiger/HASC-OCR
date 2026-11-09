"""Certificates for the website's HTTPS on the office PC (spec-1.1.0 section 6, job B).

    python -m hasc.certs --dir C:\\HASC\\config --name OFFICE-PC --ip 192.168.1.10 --ip 192.168.2.15

Makes a certificate authority once (ca.crt, ca.key; kept across updates, since every phone trusts it)
and a server certificate for the PC's name, localhost, 127.0.0.1 and the given addresses (server.crt,
server.key). A later run makes a new server certificate only when the names or addresses changed, it
was not signed by this authority, or it runs out within a year. The installer runs this as hasc, so
the keys are encrypted to it like the rest of the config folder.
"""
import argparse
import datetime
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_NAME = "HASC local CA"
CA_DAYS = 20 * 365
# Apple refuses TLS server certificates valid for more than 825 days, even under a private authority.
SERVER_DAYS = 800
# Renewed in its last year, so an update at least once a year always lands in the window.
RENEW_BEFORE = datetime.timedelta(days=365)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _new_key() -> rsa.RSAPrivateKey:
    # RSA rather than EC: older Android phones in the factory may not take EC authorities.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def load_or_create_ca(folder: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey, bool]:
    """Return the authority and its key, making them only if either file is missing."""
    crt_path, key_path = folder / "ca.crt", folder / "ca.key"
    if crt_path.is_file() != key_path.is_file():
        # Making a new authority would make every phone trust the website again: stop instead.
        raise ValueError(f"only one of ca.crt and ca.key is in {folder}; restore the other from a backup")
    if crt_path.is_file() and key_path.is_file():
        cert = x509.load_pem_x509_certificate(crt_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if key.public_key().public_numbers() != cert.public_key().public_numbers():
            raise ValueError(f"ca.key does not belong to ca.crt in {folder}; restore the pair from a backup")
        return cert, key, False

    key = _new_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME)])
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write(key_path, _key_pem(key))
    _write(crt_path, cert.public_bytes(serialization.Encoding.PEM))
    return cert, key, True


def wanted_names(name: str, ips: list[str]) -> tuple[set[str], set[str]]:
    """The DNS names and IP addresses the server certificate must cover."""
    dns = {"localhost"}
    if name:
        dns.add(name.lower())
    addresses = {"127.0.0.1"} | {str(ipaddress.ip_address(ip)) for ip in ips}
    return dns, addresses


def _covered(cert: x509.Certificate) -> tuple[set[str], set[str]]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set(), set()
    dns = {d.lower() for d in san.get_values_for_type(x509.DNSName)}
    ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    return dns, ips


def server_cert_is_current(folder: Path, ca: x509.Certificate, dns: set[str], ips: set[str]) -> bool:
    crt_path, key_path = folder / "server.crt", folder / "server.key"
    if not (crt_path.is_file() and key_path.is_file()):
        return False
    cert = x509.load_pem_x509_certificate(crt_path.read_bytes())
    if cert.issuer != ca.subject:
        return False
    try:
        cert.verify_directly_issued_by(ca)
    except (ValueError, TypeError, InvalidSignature):
        return False
    if cert.not_valid_after_utc - _now() < RENEW_BEFORE:
        return False
    # The key is written before the certificate, so a cut between the two leaves a pair that differs.
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if key.public_key().public_numbers() != cert.public_key().public_numbers():
        return False
    return _covered(cert) == (dns, ips)


def create_server_cert(folder: Path, ca: x509.Certificate, ca_key, name: str, dns: set[str], ips: set[str]) -> x509.Certificate:
    key = _new_key()
    now = _now()
    alt_names = [x509.DNSName(d) for d in sorted(dns)] + [
        x509.IPAddress(ipaddress.ip_address(ip)) for ip in sorted(ips)
    ]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name or "localhost")]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=SERVER_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write(folder / "server.key", _key_pem(key))
    _write(folder / "server.crt", cert.public_bytes(serialization.Encoding.PEM))
    return cert


def ensure_certificates(folder: Path, name: str, ips: list[str]) -> dict:
    """Make whatever is missing or out of date; return what was done."""
    folder.mkdir(parents=True, exist_ok=True)
    ca, ca_key, ca_created = load_or_create_ca(folder)
    dns, addresses = wanted_names(name, ips)
    server_created = False
    if ca_created or not server_cert_is_current(folder, ca, dns, addresses):
        create_server_cert(folder, ca, ca_key, name, dns, addresses)
        server_created = True
    return {"ca_created": ca_created, "server_created": server_created}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make the website's HTTPS certificates.")
    parser.add_argument("--dir", required=True, help="Folder for ca.crt, ca.key, server.crt, server.key.")
    parser.add_argument("--name", default="", help="The PC's name.")
    parser.add_argument("--ip", action="append", default=[], help="An address of the PC; repeat for each.")
    args = parser.parse_args(argv)
    try:
        result = ensure_certificates(Path(args.dir), args.name, args.ip)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print("certificate authority: " + ("created" if result["ca_created"] else "kept"))
    print("server certificate: " + ("created" if result["server_created"] else "kept"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
