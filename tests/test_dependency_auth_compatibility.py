"""Exercise real JWT/JWKS validation when upgrading the auth dependencies."""

import asyncio
import json
import time

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from evander.admin.web.shared import (
    AdminWebAuth,
    AdminWebBrandingConfig,
    AdminWebConfig,
    AzureSsoConfig,
)
from evander.admin.web.user_store import AdminWebUserStore


@pytest.fixture(scope="module")
def signing_keys():
    return tuple(rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))


@pytest.mark.parametrize(
    "case, expected_error",
    [
        ("valid", None),
        ("signature", jwt.InvalidSignatureError),
        ("audience", jwt.InvalidAudienceError),
        ("issuer", jwt.InvalidIssuerError),
        ("expired", jwt.ExpiredSignatureError),
        ("missing_subject", jwt.MissingRequiredClaimError),
        ("algorithm", jwt.InvalidAlgorithmError),
        ("tenant", ValueError),
    ],
)
def test_azure_sso_verifies_real_jwks_and_claims(tmp_path, monkeypatch, signing_keys, case, expected_error):
    azure = AzureSsoConfig(
        tenant_id="test-tenant", client_id="test-client", client_secret="test-secret",
        redirect_uri="https://admin.example.test/callback",
        authority_base_url="https://login.example.test",
        bootstrap_users=frozenset({"owner@example.test"}), post_login_redirect="/",
    )
    config = AdminWebConfig(
        auth_mode="azure_sso", username="admin", password=None,
        session_secret="test-session-secret", session_ttl_seconds=3600,
        cookie_name="test-session", cookie_secure=True, workflow_event_machine_tokens=(),
        azure_sso=azure, branding=AdminWebBrandingConfig.from_env(), branding_asset_dir=None,
    )
    auth = AdminWebAuth(config, AdminWebUserStore(tmp_path / "users.db"))
    issuer = "https://login.example.test/test-tenant/v2.0"
    jwks_uri = "https://login.example.test/keys"
    auth._azure_metadata_cache = {"issuer": issuer, "jwks_uri": jwks_uri}
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_keys[0].public_key()))
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    client = jwt.PyJWKClient(jwks_uri)
    monkeypatch.setattr(client, "fetch_data", lambda: {"keys": [public_jwk]})
    monkeypatch.setattr(jwt, "PyJWKClient", lambda _uri: client)
    now = int(time.time())
    claims = {"sub": "test-user", "iss": issuer, "aud": "test-client", "tid": "test-tenant",
              "iat": now - 60, "nbf": now - 60, "exp": now + 300}
    if case == "audience":
        claims["aud"] = "another-client"
    elif case == "issuer":
        claims["iss"] = "https://another.example.test/issuer"
    elif case == "expired":
        claims["exp"] = now - 1
    elif case == "missing_subject":
        del claims["sub"]
    elif case == "tenant":
        claims["tid"] = "another-tenant"
    key = signing_keys[1] if case == "signature" else signing_keys[0]
    algorithm = "RS256"
    if case == "algorithm":
        algorithm, key = "HS256", "test-hmac-key-that-must-not-be-accepted"
    token = jwt.encode(claims, key, algorithm=algorithm, headers={"kid": "test-key"})
    if expected_error is None:
        result = asyncio.run(auth._validate_azure_id_token(token))
        assert result["sub"] == "test-user"
    else:
        with pytest.raises(expected_error):
            asyncio.run(auth._validate_azure_id_token(token))
