import pytest

from app.utils.mac import (
    InvalidMacError,
    from_radius_format,
    normalise_mac,
    to_radius_format,
)


def test_normalises_case_and_dashes():
    assert normalise_mac("a4:c3:f0:88:91:2e") == "A4:C3:F0:88:91:2E"
    assert normalise_mac("A4-C3-F0-88-91-2E") == "A4:C3:F0:88:91:2E"


def test_radius_round_trip():
    assert to_radius_format("A4:C3:F0:88:91:2E") == "A4-C3-F0-88-91-2E"
    assert from_radius_format("A4-C3-F0-88-91-2E") == "A4:C3:F0:88:91:2E"


@pytest.mark.parametrize("bad", ["A4:C3:F0:88:91", "ZZ:C3:F0:88:91:2E", "", "A4C3F0889 12E"])
def test_rejects_malformed(bad):
    with pytest.raises(InvalidMacError):
        normalise_mac(bad)
