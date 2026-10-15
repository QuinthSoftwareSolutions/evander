"""Worker-owned authentication policy for brokered native Codex processes."""

from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

RUNTIME_PROVIDER_ID = "evander_subscription"

# Mounted read-only at /etc/codex for the lifetime of the job. Unlike ordinary
# config flags, native requirements also constrain resumed and child threads.
RUNTIME_REQUIREMENTS_TOML = """allowed_login_methods = ["chatgpt"]
cli_auth_credentials_store = "ephemeral"
model_provider = "evander_subscription"
"""

RUNTIME_REQUIRED_VALUES = {
    "allowedLoginMethods": ["chatgpt"],
    "cliAuthCredentialsStore": "ephemeral",
    "modelProvider": RUNTIME_PROVIDER_ID,
}

RUNTIME_CONFIG_OVERRIDES = [
    f'model_provider="{RUNTIME_PROVIDER_ID}"',
    'forced_login_method="chatgpt"',
    'cli_auth_credentials_store="ephemeral"',
    'openai_base_url="https://api.openai.com/v1"',
    'experimental_thread_store={type="local"}',
]


def verify_runtime_requirements(result):
    requirements = result.get("requirements") or {}
    if any(requirements.get(key) != value for key, value in RUNTIME_REQUIRED_VALUES.items()):
        raise ValueError("Native Codex is missing the worker authentication policy")


def prepare_runtime_policy(directory):
    """Prepare the job's placeholder-only TLS leg; never copy subscription keys."""
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    (directory / "requirements.toml").write_text(RUNTIME_REQUIREMENTS_TOML)
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Evander runtime loopback")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "loopback-ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "loopback-cert.pem").write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
    )
    (directory / "loopback-key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    # All of these files are available to the scanner. Their TLS key authenticates
    # only its local placeholder transport, never the worker/upstream connection.
    for path in directory.iterdir():
        path.chmod(0o644)
