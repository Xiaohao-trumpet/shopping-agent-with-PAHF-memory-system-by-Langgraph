from backend.admin_store import AdminStore


def test_default_admin_can_login(tmp_path):
    store = AdminStore(
        db_path=str(tmp_path / "admin.db"),
        default_username="admin",
        default_password="secret",
        session_ttl_seconds=600,
    )

    session = store.authenticate("admin", "secret")

    assert session is not None
    assert session["token_type"] == "bearer"
    assert session["user"]["username"] == "admin"
    assert session["user"]["role"] == "admin"
    assert store.get_session(session["access_token"])["user"]["username"] == "admin"


def test_wrong_password_is_rejected(tmp_path):
    store = AdminStore(
        db_path=str(tmp_path / "admin.db"),
        default_username="admin",
        default_password="secret",
    )

    assert store.authenticate("admin", "bad-password") is None
    assert store.authenticate("missing", "secret") is None


def test_logout_invalidates_session(tmp_path):
    store = AdminStore(
        db_path=str(tmp_path / "admin.db"),
        default_username="admin",
        default_password="secret",
    )
    session = store.authenticate("admin", "secret")
    token = session["access_token"]

    store.logout(token)

    assert store.get_session(token) is None


def test_signed_session_survives_new_store_instance(tmp_path):
    first_store = AdminStore(
        db_path=str(tmp_path / "first.db"),
        default_username="admin",
        default_password="secret",
        session_secret="stable-secret",
    )
    session = first_store.authenticate("admin", "secret")

    second_store = AdminStore(
        db_path=str(tmp_path / "second.db"),
        default_username="admin",
        default_password="secret",
        session_secret="stable-secret",
    )

    restored = second_store.get_session(session["access_token"])
    assert restored is not None
    assert restored["user"]["username"] == "admin"
