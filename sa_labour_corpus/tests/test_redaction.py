"""POPIA redaction tests. Precision matters as much as recall here."""

from __future__ import annotations

import pytest

from labour_corpus.parse.redaction import PLACEHOLDER, _plausible_sa_id, redact


def _valid_sa_id() -> str:
    """Build an identity number with a correct Luhn check digit."""
    for digit in range(10):
        candidate = "800101500108" + str(digit)
        if _plausible_sa_id(candidate):
            return candidate
    raise AssertionError("could not construct a valid test identity number")


# -- what must be removed --------------------------------------------------
def test_redacts_a_valid_sa_identity_number():
    report = redact(f"His identity number is {_valid_sa_id()}.")
    assert PLACEHOLDER in report.text
    assert report.counts["sa_id_number"] == 1
    assert report.redacted


@pytest.mark.parametrize("separator", [" ", " "])
def test_redacts_a_valid_identity_number_printed_in_groups(separator):
    """Deponents' IDs are commonly printed '800101 5009 087'."""
    v = _valid_sa_id()
    grouped = separator.join((v[:6], v[6:10], v[10:]))
    report = redact(f"ID number {grouped}.")
    assert report.counts["sa_id_number"] == 1
    assert report.text == f"ID number {PLACEHOLDER}."
    assert v[:6] not in report.text


def test_redacts_phone_numbers():
    report = redact("Call +27 82 555 1234 or 011 555 9876.")
    assert report.counts["phone_number"] == 2
    assert "555" not in report.text


def test_redacts_email_addresses():
    report = redact("Write to applicant.name@example.co.za for details.")
    assert report.counts["email_address"] == 1
    assert "@example.co.za" not in report.text


def test_redacts_labelled_bank_account_numbers():
    report = redact("The bank account number: 1234567890 was debited.")
    assert report.counts["bank_account"] == 1
    assert "1234567890" not in report.text


def test_redacts_labelled_tax_and_passport_numbers():
    report = redact("Income tax reference number: 0123456789.")
    assert report.counts.get("tax_or_passport") == 1


# -- what must be preserved ------------------------------------------------
@pytest.mark.parametrize("must_survive", [
    "JR 1487/21",
    "Act 66 of 1995",
    "section 189A",
    "[2024] ZALCJHB 212",
    "R 1 250 000.00",
    "12 months remuneration",
    "GG 53294",
])
def test_preserves_legal_identifiers(must_survive):
    """Over-redaction would destroy the corpus, so precision is tested."""
    text = f"Reference: {must_survive} in this matter."
    assert must_survive in redact(text).text


def test_does_not_redact_a_thirteen_digit_number_that_is_not_an_identity_number():
    report = redact("The employer cited 1234567890123 as an invoice reference.")
    assert "1234567890123" in report.text
    assert "sa_id_number" not in report.counts


@pytest.mark.parametrize("must_survive", [
    "800101 5001 085",      # grouped like an ID, but the checksum fails
    "800101 5001 08",       # 12 digits: not an identity number
    "8001015001085",        # contiguous, bad checksum
])
def test_does_not_redact_grouped_digits_that_fail_validation(must_survive):
    report = redact(f"Reference {must_survive} in the record.")
    assert must_survive in report.text
    assert "sa_id_number" not in report.counts


def test_grouped_identity_digits_never_join_across_a_line_or_tab():
    """Digit runs split by a tab or a line break are table cells, not one number."""
    v = _valid_sa_id()
    for separator in ("\t", "\n"):
        text = f"{v[:6]}{separator}{v[6:10]}{separator}{v[10:]}"
        assert redact(text).text == text


def test_does_not_redact_an_unlabelled_account_like_number():
    report = redact("Order number 9988776655 was placed.")
    assert "9988776655" in report.text


def test_clean_text_reports_no_redaction():
    report = redact("The dismissal was procedurally unfair.")
    assert report.redacted is False
    assert report.total == 0


def test_empty_text_is_handled():
    report = redact("")
    assert report.text == ""
    assert report.redacted is False


# -- sensitive context -----------------------------------------------------
@pytest.mark.parametrize("phrase", [
    "The complainant is a minor child.",
    "This is a sexual offence matter.",
    "The identity of the complainant may not be published.",
])
def test_flags_sensitive_context_for_extra_care(phrase):
    assert redact(phrase).sensitive_context is True


def test_ordinary_judgment_is_not_flagged_sensitive():
    assert redact("The applicant was dismissed for misconduct.").sensitive_context is False


# -- identity number validation --------------------------------------------
def test_identity_validation_requires_date_citizenship_and_checksum():
    assert _plausible_sa_id(_valid_sa_id()) is True
    assert _plausible_sa_id("9999999999999") is False   # impossible month
    assert _plausible_sa_id("800101500109") is False    # wrong length
    assert _plausible_sa_id("8001015001085") is False   # bad checksum


def test_redaction_is_reported_per_field():
    text = (
        f"ID {_valid_sa_id()}, phone 011 555 9876, "
        "email a@b.co.za, account number: 1234567890"
    )
    counts = redact(text).counts
    assert set(counts) == {"sa_id_number", "phone_number", "email_address", "bank_account"}
