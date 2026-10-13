"""The statutory provisions the navigator relies on, quoted verbatim.

Each quote was copied from the consolidated Labour Relations Act and
Employment Equity Act texts held in this corpus. The grounding check
(``grounding.py``) re-finds every quote in a held, non-draft document at
assessment time, so a quote that drifts from the statute is caught rather than
silently relied on. Keep quotes short and exact: a quote that does not appear
in the statute is worse than no quote at all.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Provision:
    key: str
    act: str            # "LRA" or "EEA": selects which held documents to search
    cite: str
    quote: str


LRA = "LRA"
EEA = "EEA"

# Title fragments that identify a held copy of each Act.
ACT_TITLES: dict[str, tuple[str, ...]] = {
    LRA: ("labour relations act",),
    EEA: ("employment equity act", "employment equity 1998"),
}

_ALL: tuple[Provision, ...] = (
    Provision("lra_186_1_e", LRA, "LRA s186(1)(e)",
              "an employee terminated employment with or without notice because the "
              "employer made continued employment intolerable for the employee"),
    Provision("lra_186_2", LRA, "LRA s186(2)",
              "means an unfair act or omission that arises between an employer and an "
              "employee"),
    Provision("lra_187_1", LRA, "LRA s187(1)",
              "a dismissal is automatically unfair if the employer, in dismissing the "
              "employee"),
    Provision("lra_188_1", LRA, "LRA s188(1)",
              "a dismissal that is not automatically unfair, is unfair if the employer "
              "fails to prove"),
    Provision("lra_191_1_a", LRA, "LRA s191(1)(a)",
              "if the parties to the dispute fall within the registered scope of that "
              "council"),
    Provision("lra_191_1_b_i", LRA, "LRA s191(1)(b)(i)",
              "30 days of the date of a dismissal or, if it is a later date, within 30 "
              "days of the employer making a final decision to dismiss or uphold the "
              "dismissal"),
    Provision("lra_191_1_b_ii", LRA, "LRA s191(1)(b)(ii)",
              "90 days of the date of the act or omission which allegedly constitutes "
              "the unfair labour practice"),
    Provision("lra_191_2", LRA, "LRA s191(2)",
              "if the employee shows good cause at any time, the council or the "
              "commission may permit the employee to refer the dispute after the "
              "relevant time limit has expired"),
    Provision("lra_191_5_a", LRA, "LRA s191(5)(a)",
              "the council or the commission must arbitrate the dispute at the request "
              "of the employee"),
    Provision("lra_191_5_b", LRA, "LRA s191(5)(b)",
              "the employee may refer the dispute to the labour court for adjudication "
              "if the employee has alleged that the reason for dismissal is"),
    Provision("lra_191_5A", LRA, "LRA s191(5A)",
              "must commence the arbitration immediately after certifying that the "
              "dispute remains unresolved"),
    Provision("lra_191_11", LRA, "LRA s191(11)(a)",
              "within 90 days after the council or (as the case may be) the "
              "commissioner has certified that the dispute remains unresolved"),
    Provision("lra_191_12", LRA, "LRA s191(12)",
              "may elect to refer the dispute either to arbitration or to the labour "
              "court"),
    Provision("lra_191_13", LRA, "LRA s191(13)",
              "subjected to an occupational detriment by the employer in contravention "
              "of section 3 of the protected disclosures act"),
    Provision("lra_136_1_b", LRA, "LRA s136(1)(b)",
              "within 90 days after the date on which that certificate was issued, any "
              "party to the dispute has requested that the dispute be resolved through "
              "arbitration"),
    Provision("lra_192_2", LRA, "LRA s192(2)",
              "the employer must prove that the dismissal is fair"),
    Provision("lra_194_1", LRA, "LRA s194(1)",
              "may not be more than the equivalent of 12 months' remuneration"),
    Provision("lra_194_3", LRA, "LRA s194(3)",
              "not more than the equivalent of 24 months' remuneration"),
    Provision("lra_194_4", LRA, "LRA s194(4)",
              "in respect of an unfair labour practice must be just and equitable in "
              "all the circumstances, but not more than the equivalent of 12 months "
              "remuneration"),
    Provision("eea_10_2", EEA, "EEA s10(2)",
              "within six months after the act or omission that allegedly constitutes "
              "unfair discrimination"),
    Provision("eea_10_3", EEA, "EEA s10(3)",
              "may at any time permit a party that shows good cause to refer a dispute "
              "after the relevant time limit"),
    Provision("eea_10_6_a", EEA, "EEA s10(6)(a)",
              "any party to the dispute may refer it to the labour court for "
              "adjudication"),
)

PROVISIONS: dict[str, Provision] = {p.key: p for p in _ALL}


def get(key: str) -> Provision:
    """Look up a provision; an unknown key is a programming error."""
    try:
        return PROVISIONS[key]
    except KeyError:
        raise KeyError(f"unknown provision {key!r}") from None
