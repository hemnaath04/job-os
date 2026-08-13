"""The `contact` fact built from JSON Resume `basics`.

Before this existed, `basics` was the one section the importer threw away, so
name, phone, address and profile links never reached `profile_facts` at all.
The autofill extension reads every value it types from that vault and applies
the same `verified` gate to all of it, so contact details had to live there too
rather than being special-cased somewhere with weaker rules.

Everything here is about copying verbatim and refusing to guess.
"""
from job_os.services.profile_import import _contact_fact, _facts_from_json_resume


def _basics() -> dict:
    return {
        "basics": {
            "name": "Ada Lovelace",
            "label": "Software Engineer",
            "email": "ada@example.com",
            "phone": "+1 617 555 0142",
            "url": "https://ada.example.com",
            "location": {
                "address": "12 Analytical Way",
                "city": "Boston",
                "region": "MA",
                "postalCode": "02115",
                "countryCode": "US",
            },
            "profiles": [
                {"network": "LinkedIn", "url": "https://linkedin.com/in/adalovelace"},
                {"network": "GitHub", "url": "https://github.com/adalovelace"},
            ],
        }
    }


def test_contact_fact_copies_every_value_verbatim() -> None:
    fact = _contact_fact(_basics())

    assert fact is not None
    assert fact.kind == "contact"
    assert fact.title == "Ada Lovelace"

    payload = fact.payload
    assert payload["name"] == "Ada Lovelace"
    assert payload["email"] == "ada@example.com"
    assert payload["phone"] == "+1 617 555 0142"
    assert payload["address"] == "12 Analytical Way"
    assert payload["city"] == "Boston"
    assert payload["region"] == "MA"
    assert payload["postalCode"] == "02115"
    assert payload["countryCode"] == "US"


def test_profile_links_are_keyed_by_lowercased_network() -> None:
    payload = _contact_fact(_basics()).payload

    assert payload["profiles"] == {
        "linkedin": "https://linkedin.com/in/adalovelace",
        "github": "https://github.com/adalovelace",
    }


def test_a_profile_entry_missing_a_url_is_dropped_not_invented() -> None:
    doc = _basics()
    doc["basics"]["profiles"] = [
        {"network": "LinkedIn", "url": ""},
        {"network": "", "url": "https://example.com/x"},
        {"network": "GitHub", "url": "https://github.com/adalovelace"},
    ]

    payload = _contact_fact(doc).payload

    # Only the complete pair survives. A network with no URL must not become a
    # URL guessed from the person's name.
    assert payload["profiles"] == {"github": "https://github.com/adalovelace"}


def test_no_name_means_no_contact_fact() -> None:
    assert _contact_fact({"basics": {"email": "ada@example.com"}}) is None
    assert _contact_fact({"basics": {}}) is None
    assert _contact_fact({}) is None


def test_missing_location_block_leaves_fields_absent_rather_than_blank() -> None:
    payload = _contact_fact({"basics": {"name": "Ada Lovelace"}}).payload

    # None, not "", so the extension's normalizer treats them as absent and
    # leaves the corresponding form fields alone.
    assert payload["city"] is None
    assert payload["postalCode"] is None
    assert payload["profiles"] == {}


def test_contact_fact_is_emitted_first_and_only_once() -> None:
    doc = _basics()
    doc["education"] = [{"institution": "Northeastern University", "area": "Computer Science"}]

    facts = [fact for fact, _ in _facts_from_json_resume(doc)]
    contacts = [f for f in facts if f.kind == "contact"]

    assert len(contacts) == 1
    assert facts[0].kind == "contact"


def test_import_without_basics_still_works() -> None:
    facts = [fact for fact, _ in _facts_from_json_resume({"education": [{"institution": "NEU"}]})]

    assert all(f.kind != "contact" for f in facts)
    assert len(facts) == 1
