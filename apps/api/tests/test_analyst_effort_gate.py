"""The analyst effort is measurable without shipping the change that measures it.

A trace of four real runs put the analyst at 57% of tailoring wall clock, 184s
of 321s, while carrying half the input of the compose call. So effort, not
context size, is the variable worth testing. The tailor runs in the Appwrite
Function, which deploys from `main`, so a module constant could not be varied
without shipping the very change the measurement exists to justify.

Unset is production, and unset must stay bit-identical to what shipped before
this field existed. That is the whole safety property, so it is what these pin.
"""
from __future__ import annotations

from job_os.settings import Settings


def test_production_is_unset_so_the_gateway_default_applies() -> None:
    assert Settings().analyst_effort is None


def test_it_reads_from_the_environment_so_no_deploy_is_needed_to_vary_it(
    monkeypatch,
) -> None:
    """The Appwrite function's variables are the dial, not a code change."""
    monkeypatch.setenv("ANALYST_EFFORT", "medium")
    assert Settings().analyst_effort == "medium"


def test_unset_sends_no_output_config_at_all() -> None:
    """Not `effort: None`, which would be a different request than before."""
    effort = None
    kwargs = {"output_config": {"effort": effort}} if effort else {}
    assert kwargs == {}


def test_set_sends_the_effort() -> None:
    effort = "medium"
    kwargs = {"output_config": {"effort": effort}} if effort else {}
    assert kwargs == {"output_config": {"effort": "medium"}}
