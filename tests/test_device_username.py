"""Each device signs in with its own RADIUS username, and account cleanup matches them all."""

from unittest.mock import MagicMock

from app.services.radius import (
    ACCOUNT_MATCH,
    account_match_params,
    device_username,
    remove_user_access,
)


def test_device_username_format():
    assert device_username("a@mapoly.edu.ng", "a4:c3:f0:88:91:2e") == "a@mapoly.edu.ng|A4-C3-F0-88-91-2E"


def test_two_devices_get_two_usernames():
    one = device_username("a@mapoly.edu.ng", "A4:C3:F0:88:91:01")
    two = device_username("a@mapoly.edu.ng", "A4:C3:F0:88:91:02")
    assert one != two


def test_account_match_params_use_a_prefix_not_a_like_pattern():
    # Wildcard characters in an email must be treated as plain text, not as LIKE wildcards.
    params = account_match_params("john_doe%@mapoly.edu.ng")
    assert params["p"] == "john_doe%@mapoly.edu.ng|"
    assert params["n"] == len(params["p"])


def test_cleanup_does_not_remove_a_similar_account():
    # Real SQL on SQLite: removing "a_b@x.edu" must leave "axb@x.edu" alone.
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE radcheck (id INTEGER PRIMARY KEY AUTOINCREMENT, username VARCHAR(255),"
            " attribute VARCHAR(64), op CHAR(2), value VARCHAR(253))"
        ))
        for name in ("radreply", "radusergroup"):
            conn.execute(text(
                f"CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, username VARCHAR(255),"
                " attribute VARCHAR(64), op CHAR(2), value VARCHAR(253))"
            ))
        conn.execute(text(
            "INSERT INTO radcheck (username, attribute, op, value) VALUES "
            "('a_b@x.edu|A4-C3-F0-88-91-01', 'Cleartext-Password', ':=', 't1'),"
            "('axb@x.edu|A4-C3-F0-88-91-02', 'Cleartext-Password', ':=', 't2'),"
            "('a_b@x.edu', 'Cleartext-Password', ':=', 't3')"
        ))
    with Session(engine) as session:
        remove_user_access(session, "a_b@x.edu")
        left = [r[0] for r in session.execute(text("SELECT username FROM radcheck")).all()]
    assert left == ["axb@x.edu|A4-C3-F0-88-91-02"]


def test_account_cleanup_covers_every_device_row():
    radius = MagicMock()
    remove_user_access(radius, "a@mapoly.edu.ng")
    statements = [c[0][0] for c in radius.execute.call_args_list]
    assert len(statements) == 3  # radcheck, radreply, radusergroup
    for stmt in statements:
        assert ACCOUNT_MATCH in str(stmt)
    radius.commit.assert_called_once()
