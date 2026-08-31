"""A held provider is not crawled by default, and is not deleted either.

`bamboohr` is held: 4,992 boards of small employers whose value to a search
nobody has measured, in an index that has to fit a 500 MB storage budget. That
is a product decision pending evidence, not a judgement about the provider or
its seed file, which is the most carefully built one in the package (every
token confirmed by fetching it, harvested from Common Crawl rather than
guessed).

What makes it a hold rather than a deletion is that all four of these stay
true, so every one of them is a test here:

  * the seed file is still in the package and still readable
  * the provider is still registered
  * asking for it by name still crawls it
  * the corpus summary still reports it, and says how much is being held

The fourth matters most for a decision that is meant to be revisited: a summary
that quietly stopped mentioning BambooHR would make the hold indistinguishable
from having dropped it, which is exactly the state this arrangement exists to
avoid.
"""
from __future__ import annotations

import pytest

from job_os.ingest import corpus
from job_os.ingest.providers import PROVIDER_NAMES


def test_bamboohr_is_held_and_the_others_are_not() -> None:
    assert corpus.HELD_PROVIDERS == frozenset({"bamboohr"})
    for provider in ("greenhouse", "lever", "ashby", "workday", "oracle_cloud"):
        assert provider in corpus.DEFAULT_CRAWL_PROVIDERS
    assert "bamboohr" not in corpus.DEFAULT_CRAWL_PROVIDERS


def test_every_provider_is_either_crawled_or_explicitly_held() -> None:
    """No third state. A provider that fell out of both lists would stop being
    crawled with nothing saying so, which is the failure mode a hold is
    supposed to replace."""
    assert set(corpus.DEFAULT_CRAWL_PROVIDERS) | corpus.HELD_PROVIDERS == set(PROVIDER_NAMES)


def test_the_default_token_list_excludes_the_held_provider() -> None:
    providers = {token.provider for token in corpus.seed_tokens()}
    assert "bamboohr" not in providers
    assert "greenhouse" in providers


def test_asking_for_a_held_provider_by_name_still_returns_its_tokens() -> None:
    """The reversal path a `--providers bamboohr` run depends on. If the hold
    were a block rather than a default, there would be no way to gather the
    evidence the hold is waiting for."""
    tokens = corpus.seed_tokens(["bamboohr"])

    assert len(tokens) > 4_000
    assert {token.provider for token in tokens} == {"bamboohr"}


def test_the_seed_file_is_still_there(monkeypatch: pytest.MonkeyPatch) -> None:
    """Held, not deleted. `_bulk_tokens` reads the packaged file directly, so
    this fails if the file is ever removed to "clean up" the hold."""
    assert len(corpus._bulk_tokens("bamboohr")) > 4_000


def test_the_summary_reports_the_held_provider_and_what_it_costs() -> None:
    """`total` is what a default sweep would crawl; `held_total` is what it
    would not. Both are needed, or a reader has to subtract two numbers to
    discover that a tenth of the corpus is sitting idle."""
    summary = corpus.corpus_summary()

    assert summary["bamboohr"] > 4_000, "the held provider is still counted by name"
    assert summary["held_total"] == summary["bamboohr"]
    assert summary["total"] == len(corpus.seed_tokens())
    assert summary["total"] < summary["bamboohr"] + summary["total"]


def test_the_sweep_resolves_the_default_rather_than_passing_none_through() -> None:
    """`due_tokens` reads `ats_board_tokens`, where a held provider's rows are
    already sitting from an earlier sweep. A default that lived only in
    `corpus.seed_tokens` would stop SEEDING bamboohr and go on CRAWLING it, so
    the resolution has to happen in `run_sweep` and be handed to both."""
    import inspect

    from job_os.ingest import worker

    source = inspect.getsource(worker.run_sweep)
    assert "corpus.DEFAULT_CRAWL_PROVIDERS" in source
    assert "providers=crawl_providers" in source
    assert "seed_corpus(session, crawl_providers)" in source
