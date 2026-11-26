"""The API accepts browser requests from every origin in FRONTEND_ORIGIN, and no other."""

from app.config import Settings


def test_a_single_origin_still_works():
    s = Settings(FRONTEND_ORIGIN="https://wifi.eduwifi.name.ng")
    assert s.frontend_origins == ["https://wifi.eduwifi.name.ng"]


def test_a_comma_separated_list_gives_each_origin():
    s = Settings(FRONTEND_ORIGIN="https://wifi.eduwifi.name.ng, https://mapolywifi.vercel.app,http://login.mapoly.local/")
    assert s.frontend_origins == [
        "https://wifi.eduwifi.name.ng",
        "https://mapolywifi.vercel.app",
        "http://login.mapoly.local",
    ]


def test_blank_entries_are_ignored():
    s = Settings(FRONTEND_ORIGIN=" , https://wifi.eduwifi.name.ng,, ")
    assert s.frontend_origins == ["https://wifi.eduwifi.name.ng"]
