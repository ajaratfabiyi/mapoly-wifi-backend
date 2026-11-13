"""Database setup stays in step with the models, without a migration tool."""

from app.db_setup import COA_REASONS, PLANS, RADIUS_USERNAME_TABLES
from app.models import CoaQueue


def test_coa_reason_list_matches_model():
    model_reasons = set(CoaQueue.__table__.c.reason.type.enums)
    setup_reasons = {r.strip().strip("'") for r in COA_REASONS.split(",")}
    assert model_reasons == setup_reasons


def test_four_storefront_plans_seeded():
    ids = [p[0] for p in PLANS]
    assert ids == ["plan_2days", "plan_weekly", "plan_biweekly", "plan_monthly"]
    assert all(p[3] > 0 for p in PLANS)


def test_radius_username_tables_listed():
    assert set(RADIUS_USERNAME_TABLES) == {"radcheck", "radreply", "radusergroup", "radacct"}
