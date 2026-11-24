"""Database setup stays in step with the models, without a migration tool."""

from app.db_setup import COA_REASONS, PLANS, RADIUS_USERNAME_TABLES
from app.models import CoaQueue


def test_coa_reason_list_matches_model():
    model_reasons = set(CoaQueue.__table__.c.reason.type.enums)
    setup_reasons = {r.strip().strip("'") for r in COA_REASONS.split(",")}
    assert model_reasons == setup_reasons


def test_four_storefront_plans_seeded():
    ids = [p[0] for p in PLANS]
    assert ids == ["plan_1day", "plan_2days", "plan_weekly", "plan_monthly"]
    assert all(p[3] > 0 for p in PLANS)


def test_radius_username_tables_listed():
    assert set(RADIUS_USERNAME_TABLES) == {"radcheck", "radreply", "radusergroup", "radacct"}


def test_points_prices_match_the_price_list():
    """₦ price, earned points and points price for each plan, as agreed."""
    by_id = {p[0]: p for p in PLANS}
    assert by_id["plan_1day"][3:] == (30000, None, 5, 300)
    assert by_id["plan_2days"][3:] == (50000, 25, 10, 500)
    assert by_id["plan_weekly"][3:] == (200000, 35, 20, 2000)
    assert by_id["plan_monthly"][3:] == (500000, None, 50, 5000)
    # Points cost equals the naira price, since 1 point = ₦1.
    assert all(p[6] == p[3] // 100 for p in PLANS)


def test_retired_plan_is_not_in_the_storefront_list():
    from app.db_setup import RETIRED_PLAN_IDS

    assert "plan_biweekly" in RETIRED_PLAN_IDS
    assert not {p[0] for p in PLANS} & set(RETIRED_PLAN_IDS)
