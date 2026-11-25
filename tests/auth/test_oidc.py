from app import config
from flask import url_for, session
from urllib.parse import parse_qs
from urllib3.util import parse_url
from app.auth.views.oidc import create_user
from app.utils import random_string
from unittest.mock import patch
from app.models import User

from app.config import URL, OIDC_CLIENT_ID


mock_well_known_response = {
    "authorization_endpoint": "http://localhost:7777/authorization-endpoint",
    "userinfo_endpoint": "http://localhost:7777/userinfo-endpoint",
    "token_endpoint": "http://localhost:7777/token-endpoint",
}


@patch("requests.get")
def test_oidc_login(mock_get, flask_client):
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_login"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)
    query = parse_qs(parsed.query)

    expected_redirect_url = f"{URL}/auth/oidc/callback"

    assert "code" == query["response_type"][0]
    assert OIDC_CLIENT_ID == query["client_id"][0]
    assert expected_redirect_url == query["redirect_uri"][0]


@patch("requests.get")
def test_oidc_login_next_url(mock_get, flask_client):
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None

    mock_get.return_value.json.return_value = mock_well_known_response

    with flask_client:
        r = flask_client.get(
            url_for("auth.oidc_login", next="/dashboard/settings/"),
            follow_redirects=False,
        )
        location = r.headers.get("Location")
        assert location is not None

        parsed = parse_url(location)
        query = parse_qs(parsed.query)

        expected_redirect_url = f"{URL}/auth/oidc/callback"

        assert "code" == query["response_type"][0]
        assert OIDC_CLIENT_ID == query["client_id"][0]
        assert expected_redirect_url == query["redirect_uri"][0]
        assert session["oauth_redirect_next"] == "/dashboard/settings/"


@patch("requests.get")
def test_oidc_login_no_client_id(mock_get, flask_client):
    config.OIDC_CLIENT_ID = None
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_login"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/auth/login"

    assert expected_redirect_url == parsed.path

    config.OIDC_CLIENT_ID = "to_fill"


@patch("requests.get")
def test_oidc_login_no_client_secret(mock_get, flask_client):
    config.OIDC_CLIENT_SECRET = None
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_login"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/auth/login"

    assert expected_redirect_url == parsed.path

    config.OIDC_CLIENT_SECRET = "to_fill"


@patch("requests.get")
def test_oidc_callback_no_oauth_state(mock_get, flask_client):
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = None

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is None


@patch("requests.get")
def test_oidc_callback_no_client_id(mock_get, flask_client):
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = "state"
    config.OIDC_CLIENT_ID = None

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/auth/login"

    assert expected_redirect_url == parsed.path

    config.OIDC_CLIENT_ID = "to_fill"
    with flask_client.session_transaction() as sess:
        sess["oauth_state"] = None


@patch("requests.get")
def test_oidc_callback_no_client_secret(mock_get, flask_client):
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = "state"
    config.OIDC_CLIENT_SECRET = None

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/auth/login"

    assert expected_redirect_url == parsed.path

    config.OIDC_CLIENT_SECRET = "to_fill"
    with flask_client.session_transaction() as sess:
        sess["oauth_state"] = None


@patch("requests.get")
@patch("requests_oauthlib.OAuth2Session.fetch_token")
@patch("requests_oauthlib.OAuth2Session.get")
def test_oidc_callback_invalid_user(
    mock_oauth_get, mock_fetch_token, mock_get, flask_client
):
    mock_oauth_get.return_value = MockResponse(400, {})
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = "state"

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/auth/login"

    assert expected_redirect_url == parsed.path
    assert mock_oauth_get.called

    with flask_client.session_transaction() as sess:
        sess["oauth_state"] = None


@patch("requests.get")
@patch("requests_oauthlib.OAuth2Session.fetch_token")
@patch("requests_oauthlib.OAuth2Session.get")
def test_oidc_callback_no_email(
    mock_oauth_get, mock_fetch_token, mock_get, flask_client
):
    mock_oauth_get.return_value = MockResponse(200, {})
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = "state"

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/auth/login"

    assert expected_redirect_url == parsed.path
    assert mock_oauth_get.called

    with flask_client.session_transaction() as session:
        session["oauth_state"] = None


@patch("requests.get")
@patch("requests_oauthlib.OAuth2Session.fetch_token")
@patch("requests_oauthlib.OAuth2Session.get")
def test_oidc_callback_disabled_registration(
    mock_oauth_get, mock_fetch_token, mock_get, flask_client
):
    config.DISABLE_REGISTRATION = True
    email = random_string()
    mock_oauth_get.return_value = MockResponse(200, {"email": email})
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = "state"

    mock_get.return_value.json.return_value = mock_well_known_response

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/auth/register"

    assert expected_redirect_url == parsed.path
    assert mock_oauth_get.called

    config.DISABLE_REGISTRATION = False
    with flask_client.session_transaction() as sess:
        sess["oauth_state"] = None


@patch("requests.get")
@patch("requests_oauthlib.OAuth2Session.fetch_token")
@patch("requests_oauthlib.OAuth2Session.get")
def test_oidc_callback_registration(
    mock_oauth_get, mock_fetch_token, mock_get, flask_client
):
    email = random_string()
    mock_oauth_get.return_value = MockResponse(
        200,
        {
            "email": email,
            config.OIDC_NAME_FIELD: "name",
        },
    )
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = "state"

    mock_get.return_value.json.return_value = mock_well_known_response

    user = User.get_by(email=email)
    assert user is None

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/dashboard/"

    assert expected_redirect_url == parsed.path
    assert mock_oauth_get.called

    user = User.get_by(email=email)
    assert user is not None
    assert user.email == email

    with flask_client.session_transaction() as sess:
        sess["oauth_state"] = None


@patch("requests.get")
@patch("requests_oauthlib.OAuth2Session.fetch_token")
@patch("requests_oauthlib.OAuth2Session.get")
def test_oidc_callback_login(mock_oauth_get, mock_fetch_token, mock_get, flask_client):
    email = random_string()
    mock_oauth_get.return_value = MockResponse(
        200,
        {
            "email": email,
        },
    )
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = "state"

    mock_get.return_value.json.return_value = mock_well_known_response

    user = User.create(
        email=email,
        name="name",
        password="",
        activated=True,
    )
    user = User.get_by(email=email)
    assert user is not None

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/dashboard/"

    assert expected_redirect_url == parsed.path
    assert mock_oauth_get.called

    with flask_client.session_transaction() as sess:
        sess["oauth_state"] = None


@patch("requests.get")
@patch("requests_oauthlib.OAuth2Session.fetch_token")
@patch("requests_oauthlib.OAuth2Session.get")
def test_oidc_callback_login_with_next_url(
    mock_oauth_get, mock_fetch_token, mock_get, flask_client
):
    email = random_string()
    mock_oauth_get.return_value = MockResponse(
        200,
        {
            "email": email,
        },
    )
    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = "/dashboard/settings/"
        sess["oauth_state"] = "state"

    mock_get.return_value.json.return_value = mock_well_known_response

    user = User.create(
        email=email,
        name="name",
        password="",
        activated=True,
    )
    user = User.get_by(email=email)
    assert user is not None

    r = flask_client.get(
        url_for("auth.oidc_callback"),
        follow_redirects=False,
    )
    location = r.headers.get("Location")
    assert location is not None

    parsed = parse_url(location)

    expected_redirect_url = "/dashboard/settings/"

    assert expected_redirect_url == parsed.path
    assert mock_oauth_get.called

    with flask_client.session_transaction() as sess:
        sess["oauth_state"] = None


def test_create_user():
    email = random_string()
    user = create_user(
        email,
        {
            config.OIDC_NAME_FIELD: "name",
        },
    )
    assert user.email == email
    assert user.name == "name"
    assert user.activated


class MockResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self.json_data = json_data
        self.text = "error"

    def json(self):
        return self.json_data


# --- skip SimpleLogin 2FA if the identity provider did MFA (amr claim) -------

_ISSUER = "https://idp.example.com/"
_rsa_key = None


def _rsa():
    global _rsa_key
    if _rsa_key is None:
        from jwcrypto import jwk

        _rsa_key = jwk.JWK.generate(kty="RSA", size=2048, kid="k1")
    return _rsa_key


def _id_token(claims: dict, alg="RS256", key=None, **overrides) -> str:
    import time
    from jwcrypto import jwk, jwt
    from jwcrypto.common import base64url_encode

    now = int(time.time())
    payload = {
        "iss": _ISSUER,
        "aud": config.OIDC_CLIENT_ID,
        "sub": "123",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(claims)
    payload.update(overrides)
    if key is None:
        if alg.startswith("HS"):
            key = jwk.JWK(
                kty="oct", k=base64url_encode(config.OIDC_CLIENT_SECRET.encode())
            )
        else:
            key = _rsa()
    header = {"alg": alg}
    if not alg.startswith("HS"):
        header["kid"] = key.key_id
    token = jwt.JWT(header=header, claims=payload)
    token.make_signed_token(key)
    return token.serialize()


class _RequestsGet:
    """requests.get mock answering the well-known and the JWKS URL"""

    def __init__(self):
        from unittest.mock import MagicMock

        self.well_known = MagicMock()
        self.well_known.json.return_value = {
            **mock_well_known_response,
            "issuer": _ISSUER,
            "jwks_uri": "https://idp.example.com/jwks",
        }
        self.jwks = MagicMock()
        self.jwks.text = '{"keys": [%s]}' % _rsa().export_public()

    def __call__(self, url, *args, **kwargs):
        return self.jwks if url.endswith("/jwks") else self.well_known


def _do_callback(flask_client, id_token, skip_amr):
    from unittest.mock import patch as _patch

    email = random_string()
    user = User.create(email=email, name="name", password="", activated=True)
    user.enable_otp = True
    user.otp_secret = "base32secret3232"

    config.OIDC_WELL_KNOWN_URL = "http://localhost:7777/well-known-url"
    with flask_client.session_transaction() as sess:
        sess["oauth_redirect_next"] = None
        sess["oauth_state"] = "state"

    token = {"access_token": "x", "token_type": "Bearer"}
    if id_token:
        token["id_token"] = id_token
    old_amr = config.OIDC_SKIP_MFA_AMR
    config.OIDC_SKIP_MFA_AMR = skip_amr
    try:
        with _patch("requests.get", new=_RequestsGet()), _patch(
            "requests_oauthlib.OAuth2Session.fetch_token", return_value=token
        ), _patch(
            "requests_oauthlib.OAuth2Session.get",
            return_value=MockResponse(200, {"email": email}),
        ):
            r = flask_client.get(url_for("auth.oidc_callback"), follow_redirects=False)
    finally:
        config.OIDC_SKIP_MFA_AMR = old_amr
        with flask_client.session_transaction() as sess:
            sess["oauth_state"] = None
    return parse_url(r.headers.get("Location")).path


def test_oidc_callback_skip_mfa_with_amr_mfa(flask_client):
    tok = _id_token({"amr": ["pwd", "mfa"]})
    assert _do_callback(flask_client, tok, ["mfa"]) == "/dashboard/"


def test_oidc_callback_skip_mfa_with_hs256(flask_client):
    tok = _id_token({"amr": ["mfa"]}, alg="HS256")
    assert _do_callback(flask_client, tok, ["mfa"]) == "/dashboard/"


def test_oidc_callback_mfa_when_feature_disabled(flask_client):
    tok = _id_token({"amr": ["mfa"]})
    assert _do_callback(flask_client, tok, []) == "/auth/mfa"


def test_oidc_callback_mfa_when_idp_only_password(flask_client):
    tok = _id_token({"amr": ["pwd"]})
    assert _do_callback(flask_client, tok, ["mfa"]) == "/auth/mfa"


def test_oidc_callback_mfa_without_amr(flask_client):
    tok = _id_token({})
    assert _do_callback(flask_client, tok, ["mfa"]) == "/auth/mfa"


def test_oidc_callback_mfa_without_id_token(flask_client):
    assert _do_callback(flask_client, None, ["mfa"]) == "/auth/mfa"


def test_oidc_callback_mfa_with_forged_signature(flask_client):
    from jwcrypto import jwk

    other = jwk.JWK.generate(kty="RSA", size=2048, kid="k1")
    tok = _id_token({"amr": ["mfa"]}, key=other)
    assert _do_callback(flask_client, tok, ["mfa"]) == "/auth/mfa"


def test_oidc_callback_mfa_with_wrong_audience(flask_client):
    tok = _id_token({"amr": ["mfa"]}, aud="someone-else")
    assert _do_callback(flask_client, tok, ["mfa"]) == "/auth/mfa"


def test_oidc_callback_mfa_with_wrong_issuer(flask_client):
    tok = _id_token({"amr": ["mfa"]}, iss="https://evil.example.com/")
    assert _do_callback(flask_client, tok, ["mfa"]) == "/auth/mfa"


def test_oidc_callback_mfa_with_expired_id_token(flask_client):
    import time

    tok = _id_token({"amr": ["mfa"]}, exp=int(time.time()) - 3600)
    assert _do_callback(flask_client, tok, ["mfa"]) == "/auth/mfa"
