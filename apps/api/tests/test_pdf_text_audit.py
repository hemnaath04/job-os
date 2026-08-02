"""The ATS text-layer audit: coverage, artifact patterns, fail-safety, real renders.

The claim under test is narrow and worth stating plainly, because it is the only
reason this module exists: a resume can look perfect on the page and still hand an
applicant tracking system unusable text. So the checks are tested against text,
and then the whole thing is tested against PDFs that two real engines actually
produce, because a defect that only exists in a hand-written fixture is not the
defect this product shipped.

Two tests here are about the limits rather than the successes, and they matter
more than the passing ones. `test_coverage_alone_would_miss_awesome_cv` pins the
reason the artifact patterns cannot be deleted in favour of the general check, and
`test_moderncv_tectonic_is_not_flagged` pins the template that is still served by
Tectonic in production. If either starts failing, the guard has changed character
and somebody should look at it rather than adjust the number.

The render tests use the bundled sample resume rather than checked-in PDFs. That
keeps a real person's contact details out of the repository, and it costs nothing,
because the defects belong to the templates and not to the data: `altacv` under
Tectonic leaks Font Awesome macros whoever the resume is about, and `awesome-cv`
decomposes its small caps the same way.
"""
from __future__ import annotations

import pytest

from job_os.services import latex_render, typst_render
from job_os.services.latex_catalog import SAMPLE_RESUME
from job_os.services.latex_render import tectonic_binary
from job_os.services.pdf_text_audit import audit_pdf_text, source_vocabulary
from job_os.services.typst_render import typst_binary

needs_tectonic = pytest.mark.skipif(
    tectonic_binary() is None,
    reason="tectonic is not installed on this machine",
)
needs_typst = pytest.mark.skipif(
    typst_binary() is None,
    reason="typst is not installed on this machine",
)

# The two bundled templates whose Tectonic output is known to reach an ATS
# damaged, and whose Typst port does not. Both are rendered from the same
# resume by both engines, so the only variable is the engine.
DAMAGED_UNDER_TECTONIC = ["altacv", "awesome-cv"]


def _audit_text(text: str):
    """Run the detectors over `text` by way of a stubbed extraction."""
    from job_os.services import pdf_text_audit

    return pdf_text_audit._find_artifacts(text)


# ---------------------------------------------------------------------------
# Coverage, the primary check
# ---------------------------------------------------------------------------


def test_source_vocabulary_takes_values_not_schema_keys() -> None:
    """Key names are the schema's words, not the candidate's."""
    vocabulary = source_vocabulary(
        {"basics": {"summary": "Distributed systems engineer"}}
    )
    assert "distributed" in vocabulary
    assert "summary" not in vocabulary
    assert "basics" not in vocabulary


def test_source_vocabulary_drops_contact_noise() -> None:
    """A template may render a URL as an icon. That is not a text-layer defect."""
    vocabulary = source_vocabulary(
        {"basics": {"email": "jordan@example.com", "url": "https://example.com/jordan"}}
    )
    assert vocabulary == set()


@needs_typst
def test_coverage_is_measured_against_the_source_document() -> None:
    rendered = typst_render.render_resume_pdf(SAMPLE_RESUME, template_key="moderncv")
    audit = audit_pdf_text(rendered.bytes_, source_document=SAMPLE_RESUME)

    assert audit.coverage is not None
    assert audit.coverage > 0.9
    assert audit.coverage_shortfall is False


@needs_typst
def test_coverage_is_not_measured_without_a_source_document() -> None:
    """No document, no ratio. None is not the same as zero."""
    rendered = typst_render.render_resume_pdf(SAMPLE_RESUME, template_key="jakes")
    audit = audit_pdf_text(rendered.bytes_)

    assert audit.coverage is None
    assert audit.coverage_shortfall is False


@needs_typst
def test_a_sparse_draft_does_not_get_a_coverage_ratio() -> None:
    """Too few source words and the ratio is noise, so it is not computed."""
    rendered = typst_render.render_resume_pdf(SAMPLE_RESUME, template_key="jakes")
    audit = audit_pdf_text(
        rendered.bytes_, source_document={"basics": {"name": "Jordan A. Sample"}}
    )

    assert audit.coverage is None


@needs_tectonic
@pytest.mark.slow
def test_coverage_catches_the_collapsed_altacv_render() -> None:
    """The general check, doing the job the specific patterns should not have to."""
    rendered = latex_render.render_resume_pdf(SAMPLE_RESUME, template_key="altacv")
    audit = audit_pdf_text(rendered.bytes_, source_document=SAMPLE_RESUME)

    assert audit.coverage is not None
    assert audit.coverage < 0.5
    assert audit.coverage_shortfall is True
    assert audit.clean is False


@needs_tectonic
@pytest.mark.slow
@needs_typst
def test_coverage_alone_would_miss_awesome_cv() -> None:
    """Why the artifact patterns stay, in one assertion.

    Awesome-CV's damaged Tectonic render scores 83.5% on coverage. Jake's honest
    one scores 85.0%, because Jake's legitimately omits whole sections. Those are
    1.5 points apart, so no coverage threshold separates a broken render from a
    clean one with any margin worth having, and the thing that actually catches
    Awesome-CV has to be the patterns. If this ever fails, coverage has become
    sharp enough to stand on its own and the patterns could be reconsidered.
    """
    damaged = audit_pdf_text(
        latex_render.render_resume_pdf(SAMPLE_RESUME, template_key="awesome-cv").bytes_,
        source_document=SAMPLE_RESUME,
    )
    honest = audit_pdf_text(
        typst_render.render_resume_pdf(SAMPLE_RESUME, template_key="jakes").bytes_,
        source_document=SAMPLE_RESUME,
    )

    assert damaged.coverage is not None and honest.coverage is not None
    assert abs(damaged.coverage - honest.coverage) < 0.05, (
        "damaged and honest coverage have separated; the patterns may no longer "
        f"be needed (damaged {damaged.coverage:.1%}, honest {honest.coverage:.1%})"
    )
    assert damaged.coverage_shortfall is False  # coverage does not catch it
    assert damaged.clean is False  # but the patterns do
    assert honest.clean is True


# ---------------------------------------------------------------------------
# Artifact patterns, the corroborating check
# ---------------------------------------------------------------------------


def test_flags_raw_latex_macros() -> None:
    found = _audit_text(r"\faGlobe : https://example.com \faLinkedin : jordan")
    assert any("raw LaTeX" in artifact for artifact in found)
    assert any(r"\faGlobe" in artifact for artifact in found)


def test_flags_broken_small_caps() -> None:
    found = _audit_text("MASTEROFSCiENCE, COMPUTERSCiENCE CANDiDATE")
    assert any("small caps" in artifact for artifact in found)


def test_flags_lost_word_spacing() -> None:
    found = _audit_text(
        "Ownedandextendedthecontracttestsuite "
        "Matchestwoledgersandexplainseverymismatch "
        "Wrotethecontractteststhatletthreeteamsdeploy"
    )
    assert any("joined together" in artifact for artifact in found)


def test_clean_text_is_not_flagged() -> None:
    assert (
        _audit_text(
            "Jordan A. Sample, Senior Backend Engineer. Built services in "
            "Python and Go. Master of Science, Computer Science."
        )
        == ()
    )


def test_ordinary_mixed_case_words_are_not_small_caps_damage() -> None:
    """The words a real resume contains must not read as decomposed small caps."""
    assert _audit_text("SQLite MySQL NoSQL IoT IIoT eBay GraphQL PostgreSQL SaaS") == ()


def test_one_joined_word_is_not_enough_to_flag() -> None:
    """Two columns can legitimately abut once. That is not a broken text layer."""
    found = _audit_text(
        "Sololearn BEDROCKED " + "SololearnBEDROCKEDcertificate " + "word " * 300
    )
    assert found == ()


# ---------------------------------------------------------------------------
# Fail-safety. An audit must never be the reason a render fails.
# ---------------------------------------------------------------------------


def test_garbage_bytes_return_the_neutral_result() -> None:
    audit = audit_pdf_text(b"this is not a pdf at all")
    assert audit.available is False
    assert audit.clean is True
    assert audit.encoding_issue_flagged is False


def test_empty_bytes_return_the_neutral_result() -> None:
    audit = audit_pdf_text(b"")
    assert audit.available is False
    assert audit.clean is True


def test_library_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    import pdf_inspector

    def boom(_: bytes) -> None:
        raise RuntimeError("library exploded")

    monkeypatch.setattr(pdf_inspector, "process_pdf_bytes", boom)
    audit = audit_pdf_text(b"%PDF-1.7 pretend")
    assert audit.available is False
    assert audit.clean is True


# ---------------------------------------------------------------------------
# Real renders. This is the part that proves the thing works.
# ---------------------------------------------------------------------------


@needs_tectonic
@pytest.mark.slow
@pytest.mark.parametrize("template_key", DAMAGED_UNDER_TECTONIC)
def test_flags_the_damaged_tectonic_render(template_key: str) -> None:
    rendered = latex_render.render_resume_pdf(SAMPLE_RESUME, template_key=template_key)
    audit = audit_pdf_text(rendered.bytes_, source_document=SAMPLE_RESUME)

    assert audit.available is True
    assert audit.clean is False, (
        f"{template_key} under Tectonic should be flagged, got {audit.artifacts}"
    )
    assert audit.artifacts


@needs_typst
@pytest.mark.parametrize("template_key", DAMAGED_UNDER_TECTONIC)
def test_passes_the_clean_typst_render(template_key: str) -> None:
    rendered = typst_render.render_resume_pdf(SAMPLE_RESUME, template_key=template_key)
    audit = audit_pdf_text(rendered.bytes_, source_document=SAMPLE_RESUME)

    assert audit.available is True
    assert audit.is_text_based is True
    assert audit.clean is True, (
        f"{template_key} under Typst should be clean, got {audit.artifacts}"
    )
    assert audit.text


@needs_typst
@pytest.mark.parametrize("template_key", ["jakes", "sb2nov", "deedy", "moderncv"])
def test_other_typst_templates_are_not_false_flagged(template_key: str) -> None:
    """The templates that were never broken must stay unflagged."""
    rendered = typst_render.render_resume_pdf(SAMPLE_RESUME, template_key=template_key)
    audit = audit_pdf_text(rendered.bytes_, source_document=SAMPLE_RESUME)

    assert audit.clean is True, f"{template_key}: {audit.artifacts}"


@needs_tectonic
@pytest.mark.slow
@pytest.mark.parametrize("template_key", ["jakes", "sb2nov", "deedy"])
def test_the_other_tectonic_renders_are_not_false_flagged(template_key: str) -> None:
    """The guard discriminates by defect, not by engine."""
    rendered = latex_render.render_resume_pdf(SAMPLE_RESUME, template_key=template_key)
    audit = audit_pdf_text(rendered.bytes_, source_document=SAMPLE_RESUME)

    assert audit.clean is True, f"{template_key}: {audit.artifacts}"


@needs_tectonic
@pytest.mark.slow
def test_moderncv_tectonic_is_not_flagged() -> None:
    """ModernCV is the one bundled template still served by Tectonic in production.

    It has no Typst port, so this is not a hypothetical path: it is what a user on
    ModernCV gets today. Its text layer is clean, and this test exists so that
    stays true, or so we hear about it the moment it stops being true.
    """
    rendered = latex_render.render_resume_pdf(SAMPLE_RESUME, template_key="moderncv")
    audit = audit_pdf_text(rendered.bytes_, source_document=SAMPLE_RESUME)

    assert audit.clean is True, f"moderncv under Tectonic: {audit.artifacts}"
    assert audit.coverage is not None and audit.coverage > 0.95


@needs_tectonic
@pytest.mark.slow
def test_mixed_classification_alone_does_not_flag() -> None:
    """`mixed` describes how a page was drawn, not whether it can be read.

    ModernCV under Tectonic is the live example: page two draws some text as
    vector paths, so the classification is `mixed` rather than `text_based`, while
    98% of the candidate's words are still right there in the layer. Gating on the
    classification would have flagged every ModernCV resume for nothing.
    """
    rendered = latex_render.render_resume_pdf(SAMPLE_RESUME, template_key="moderncv")
    audit = audit_pdf_text(rendered.bytes_, source_document=SAMPLE_RESUME)

    assert audit.pdf_type == "mixed"
    assert audit.is_text_based is False
    assert audit.text_layer_unreadable is False
    assert audit.clean is True


@needs_tectonic
@pytest.mark.slow
@needs_typst
@pytest.mark.parametrize("template_key", DAMAGED_UNDER_TECTONIC)
def test_the_audit_separates_the_two_engines(template_key: str) -> None:
    """The same resume, the same template, two engines, opposite verdicts.

    This is the whole point in one assertion: the audit is what tells the two
    renders apart, and it is looking at the layer the ATS reads, not the page.
    """
    damaged = audit_pdf_text(
        latex_render.render_resume_pdf(SAMPLE_RESUME, template_key=template_key).bytes_,
        source_document=SAMPLE_RESUME,
    )
    clean = audit_pdf_text(
        typst_render.render_resume_pdf(SAMPLE_RESUME, template_key=template_key).bytes_,
        source_document=SAMPLE_RESUME,
    )

    assert damaged.clean is False
    assert clean.clean is True
