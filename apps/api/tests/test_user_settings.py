"""User preference schema: validation, enum rejection, and old rows.

The last point is the one that earns its keep. `User.settings` is a JSONB blob,
so widening the schema is a change to how existing bytes are read rather than a
change to the table. Every row in production holds the original six keys and
nothing else, and the only thing standing between those rows and a broken
Settings page is that each field added since carries a default. That is asserted
here directly rather than inferred from the field declarations.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from job_os.routers.me import (
    NULLABLE_SETTING_KEYS,
    _reconcile_locations,
    _to_settings,
)
from job_os.schemas.me import SeniorityRange, UserSettings, UserSettingsPatch

# Exactly what a row written before any of this existed contains. Copied from
# alembic 0003 and the schema as it shipped, not generated from today's model,
# so that a later rename cannot quietly update the fixture it is tested against.
LEGACY_ROW: dict[str, Any] = {
    "theme": "light",
    "default_resume_id": None,
    "default_function": "swe",
    "default_level": "new-grad",
    "default_location": "Boston, MA",
    "timezone": "America/New_York",
}


def _merge(stored: dict[str, Any], patch: dict[str, Any]) -> UserSettings:
    """The PATCH /me/settings merge, minus the database.

    Mirrors `routers.me.patch_settings`: validate what is stored, apply only the
    keys the client sent, drop a null aimed at a field that cannot hold one,
    reconcile the two location fields, re-validate.
    """
    current = _to_settings(stored).model_dump(mode="json")
    sent = UserSettingsPatch.model_validate(patch).model_dump(exclude_unset=True, mode="json")
    updates = {k: v for k, v in sent.items() if v is not None or k in NULLABLE_SETTING_KEYS}
    current.update(updates)
    _reconcile_locations(current, updates)
    return UserSettings.model_validate(current)


# ── Backward compatibility ──


def test_a_row_with_only_the_original_six_fields_still_loads() -> None:
    settings = _to_settings(LEGACY_ROW)

    assert settings.theme == "light"
    assert settings.default_function == "swe"
    assert settings.default_level == "new-grad"
    assert settings.timezone == "America/New_York"


def test_an_old_row_defaults_every_new_field_to_no_opinion() -> None:
    """A row that predates the search preferences must not imply any of them.

    An empty list has to mean "no constraint" downstream, because that is the
    only thing an untouched account can honestly be read as saying.
    """
    settings = _to_settings(LEGACY_ROW)

    assert settings.target_titles == []
    # The struct's own defaults ARE the "never stated" answer, and every one
    # of them refuses nothing, so an untouched account gates exactly as it did
    # before eligibility existed.
    assert settings.work_eligibility.status is None
    assert settings.work_eligibility.cpt_eligible_now is False
    assert settings.work_eligibility.needs_future_sponsorship is False
    assert settings.work_eligibility.us_person_for_export_control is True
    assert settings.salary_floor is None
    assert settings.work_models == []
    assert settings.target_companies == []
    assert settings.excluded_companies == []
    assert settings.seniority_range == SeniorityRange(min=None, max=None)
    assert settings.max_job_age_days == 30
    assert settings.salary_currency == "USD"


def test_an_empty_blob_loads_as_all_defaults() -> None:
    """`server_default='{}'` means a user who never opened Settings has this."""
    assert _to_settings({}) == _to_settings(None)


def test_an_old_rows_single_location_seeds_the_new_list() -> None:
    """The reason no data migration is needed.

    Left unfilled, `locations` would read as "search anywhere" for every
    existing user, which is a behaviour change dressed up as a new field.
    """
    assert _to_settings(LEGACY_ROW).locations == ["Boston, MA"]


def test_a_patch_of_one_new_field_leaves_the_old_six_alone() -> None:
    merged = _merge(LEGACY_ROW, {"salary_floor": 120_000})

    assert merged.salary_floor == 120_000
    assert merged.theme == "light"
    assert merged.default_function == "swe"
    assert merged.default_level == "new-grad"
    assert merged.timezone == "America/New_York"


def test_the_patch_schema_covers_every_settable_field() -> None:
    """A field added to `UserSettings` and forgotten in `UserSettingsPatch` is
    readable and silently unwritable, which is worse than a missing field."""
    assert set(UserSettings.model_fields) == set(UserSettingsPatch.model_fields)


# ── Unknown keys ──


def test_unknown_keys_are_dropped_rather_than_stored() -> None:
    """The deliberate behaviour the JSONB blob depends on: a client cannot use
    settings as scratch storage."""
    settings = _to_settings({**LEGACY_ROW, "favourite_colour": "puce", "sources": ["x"]})

    assert not hasattr(settings, "favourite_colour")
    assert "favourite_colour" not in settings.model_dump()


def test_a_patch_ignores_unknown_keys_without_failing() -> None:
    merged = _merge(LEGACY_ROW, {"target_titles": ["SDE"], "junk": 1})

    assert merged.target_titles == ["SDE"]
    assert "junk" not in merged.model_dump()


# ── Enum rejection ──


@pytest.mark.parametrize(
    "field",
    ["theme", "salary_currency"],
)
def test_an_out_of_range_enum_value_is_rejected(field: str) -> None:
    with pytest.raises(ValidationError) as raised:
        UserSettingsPatch.model_validate({field: "nonsense"})

    assert field in str(raised.value)


def test_a_plausible_but_wrong_work_authorization_is_rejected() -> None:
    """"h1b" is what a client would guess. Guessing has to fail loudly, or the
    blob fills with values no filter will ever match."""
    with pytest.raises(ValidationError):
        UserSettingsPatch.model_validate({"work_eligibility": {"status": "h1b"}})


@pytest.mark.parametrize(
    "value",
    [
        "us_citizen",
        "permanent_resident",
        "f1_student",
        "visa_holder_needs_transfer",
        "needs_sponsorship",
        "other",
    ],
)
def test_every_documented_work_authorization_is_accepted(value: str) -> None:
    patched = UserSettingsPatch.model_validate({"work_eligibility": {"status": value}})
    assert patched.work_eligibility is not None
    assert patched.work_eligibility.status == value


def test_an_unknown_work_model_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UserSettingsPatch.model_validate({"work_models": ["remote", "hybrid-ish"]})


def test_an_unknown_seniority_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UserSettingsPatch.model_validate({"seniority_range": {"min": "principal"}})


def test_a_backwards_seniority_range_is_rejected() -> None:
    with pytest.raises(ValidationError) as raised:
        SeniorityRange.model_validate({"min": "senior", "max": "intern"})

    assert "ranks above" in str(raised.value)


@pytest.mark.parametrize(
    ("low", "high"),
    [("intern", "new-grad"), ("new-grad", "staff"), ("mid", "mid"), (None, "senior")],
)
def test_a_well_ordered_seniority_range_is_accepted(low: str | None, high: str) -> None:
    band = SeniorityRange.model_validate({"min": low, "max": high})

    assert (band.min, band.max) == (low, high)


@pytest.mark.parametrize("value", [-1, "lots", 10**9])
def test_an_impossible_salary_floor_is_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        UserSettingsPatch.model_validate({"salary_floor": value})


@pytest.mark.parametrize("value", [0, 200, 250_000])
def test_a_real_salary_floor_is_accepted(value: int) -> None:
    assert UserSettingsPatch.model_validate({"salary_floor": value}).salary_floor == value


def test_a_currency_code_is_case_normalised_rather_than_rejected() -> None:
    assert UserSettingsPatch.model_validate({"salary_currency": "inr"}).salary_currency == "INR"


@pytest.mark.parametrize("value", [0, 181, -5])
def test_a_job_age_outside_the_search_bounds_is_rejected(value: int) -> None:
    """These bounds match DiscoverySearchRequest.max_age_days on purpose: a
    stored preference that no search would accept is a trap, not a preference."""
    with pytest.raises(ValidationError):
        UserSettingsPatch.model_validate({"max_job_age_days": value})


# ── List hygiene ──


def test_title_lists_are_trimmed_deduped_and_order_preserving() -> None:
    settings = UserSettings.model_validate(
        {"target_titles": ["  Software Engineer ", "ML Engineer", "software engineer", "", "   "]}
    )

    assert settings.target_titles == ["Software Engineer", "ML Engineer"]


def test_a_repeated_work_model_toggle_counts_once() -> None:
    settings = UserSettings.model_validate({"work_models": ["remote", "remote", "hybrid"]})

    assert settings.work_models == ["remote", "hybrid"]


def test_a_client_cannot_grow_the_blob_without_bound() -> None:
    settings = UserSettings.model_validate(
        {
            "target_companies": [f"Company {i}" for i in range(500)],
            "target_titles": ["x" * 5_000],
        }
    )

    assert len(settings.target_companies) == 50
    assert len(settings.target_titles[0]) == 120


# ── Nulls in a PATCH ──


def test_a_null_clears_a_field_that_can_hold_one() -> None:
    """Sending null is how the Settings page says "no answer" to a select."""
    stored = {**LEGACY_ROW, "salary_floor": 120_000}

    assert _merge(stored, {"salary_floor": None}).salary_floor is None


def test_a_stored_work_authorization_from_before_the_struct_is_dropped() -> None:
    """The flat enum this replaced was declared and never read.

    `UserSettings` already ignores any key it does not declare, so a row
    written before this reads back as the default struct rather than raising.
    That is the whole migration: there is no column to alter and nothing to
    backfill, because nothing ever consumed the old value.
    """
    stored = {**LEGACY_ROW, "work_authorization": "needs_sponsorship"}

    settings = _to_settings(stored)

    assert settings.work_eligibility.status is None
    assert not hasattr(settings, "work_authorization")


def test_a_null_theme_is_ignored_rather_than_stored() -> None:
    """`theme` has a non-null default, so a merged null would fail validation
    and 500 the save. Treated as "no opinion" instead."""
    merged = _merge(LEGACY_ROW, {"theme": None})

    assert merged.theme == "light"


def test_a_null_list_is_ignored_rather_than_emptying_the_list() -> None:
    stored = {**LEGACY_ROW, "target_titles": ["SDE"]}

    assert _merge(stored, {"target_titles": None}).target_titles == ["SDE"]


def test_an_empty_list_does_clear_the_list() -> None:
    """The distinction the null rule rests on: `[]` is an answer, null is not."""
    stored = {**LEGACY_ROW, "target_titles": ["SDE"]}

    assert _merge(stored, {"target_titles": []}).target_titles == []


def test_a_patch_touches_nothing_it_did_not_send() -> None:
    stored = {**LEGACY_ROW, "target_titles": ["SDE"], "salary_floor": 100}
    merged = _merge(stored, {"theme": "dark"})

    assert merged.theme == "dark"
    assert merged.target_titles == ["SDE"]
    assert merged.salary_floor == 100


# ── The two location fields ──


def test_patching_the_old_location_field_updates_the_new_list() -> None:
    """Anything still writing `default_location` keeps working, and does not
    leave the list it seeds pointing at the previous city."""
    merged = _merge(LEGACY_ROW, {"default_location": "Seattle, WA"})

    assert merged.default_location == "Seattle, WA"
    assert merged.locations == ["Seattle, WA"]


def test_patching_the_new_list_updates_the_old_location_field() -> None:
    """The Job Finder reads `default_location` to decide what counts as local.
    Editing locations and leaving that stale would silently mis-rank results."""
    merged = _merge(LEGACY_ROW, {"locations": ["New York, NY", "Remote"]})

    assert merged.locations == ["New York, NY", "Remote"]
    assert merged.default_location == "New York, NY"


def test_clearing_the_locations_list_clears_the_old_field_too() -> None:
    merged = _merge(LEGACY_ROW, {"locations": []})

    assert merged.locations == []
    assert merged.default_location is None


def test_clearing_the_old_location_field_clears_the_list_too() -> None:
    merged = _merge(LEGACY_ROW, {"default_location": None})

    assert merged.default_location is None
    assert merged.locations == []


def test_sending_both_location_fields_takes_both_as_given() -> None:
    """A client that knows about both is trusted with both. The read-side fill
    must not overrule it, or the second field would be unwritable."""
    merged = _merge(
        LEGACY_ROW, {"default_location": "Austin, TX", "locations": ["Chicago, IL", "Remote"]}
    )

    assert merged.default_location == "Austin, TX"
    assert merged.locations == ["Chicago, IL", "Remote"]


def test_a_saved_round_trip_is_stable() -> None:
    """What the router persists must read back identically, or every save would
    drift the blob a little further from what the user typed."""
    once = _merge(
        LEGACY_ROW,
        {
            "target_titles": ["Software Engineer", "ML Engineer"],
            "work_eligibility": {
                "status": "f1_student",
                "cpt_eligible_now": True,
                "needs_future_sponsorship": True,
                "us_person_for_export_control": False,
                "clearance_eligible": False,
            },
            "salary_floor": 120_000,
            "salary_currency": "usd",
            "seniority_range": {"min": "intern", "max": "mid"},
            "work_models": ["remote", "hybrid"],
            "target_companies": ["Anthropic"],
            "excluded_companies": ["Some Consultancy"],
            "max_job_age_days": 14,
            "locations": ["Boston, MA", "Remote"],
        },
    )
    twice = UserSettings.model_validate(once.model_dump(mode="json"))

    assert twice == once
    assert twice.salary_currency == "USD"
    assert twice.default_location == "Boston, MA"


def test_a_resume_id_survives_the_json_round_trip() -> None:
    """`default_resume_id` is a UUID in the schema and a string in the blob."""
    resume_id = uuid4()
    merged = _merge(LEGACY_ROW, {"default_resume_id": str(resume_id)})

    assert merged.default_resume_id == resume_id
    assert UserSettings.model_validate(merged.model_dump(mode="json")).default_resume_id == (
        resume_id
    )
