# Dismissal Navigator legal review pack — 2026-10-13

## Status

**Automated statutory QA: complete (30/30 golden cases).**  
**Independent labour-law practitioner sign-off: pending.**

This document is preparation for professional review, not legal advice and not
a representation that a practising lawyer has approved the navigator.

## Material checked

- LRA ss 135(5), 136(1), 191(1), 191(2), 191(5), 191(5A), 191(11),
  191(12), 192 and 194 in the held consolidated LRA.
- EEA s10 in the held EEA.
- CCMA Rules information sheet (2024), especially Rules 15 and 18.
- All 30 fact patterns in `tests/golden/navigator_cases.yaml`.

## Correction made before review

The earlier implementation continued to describe the original conciliation
referral as the active step after a certificate of non-resolution was already
on file. It now:

1. treats the certificate as proof that the conciliation referral occurred;
2. omits the completed conciliation step from the current route;
3. applies LRA s191(11)'s 90-day Labour Court referral period where applicable;
4. applies LRA s136(1)(b)'s 90-day arbitration-request period where applicable;
5. describes late arbitration condonation separately from late conciliation
   referral condonation; and
6. preserves con-arb treatment for probation disputes.

## Practitioner sign-off fields

Reviewer name: ____________________  
Admission / organisation: ____________________  
Review date: ____________________

For each golden case, run `labour-corpus navigate review-sheet` and record
AGREE or DISAGREE, the corrected result, and the current provision/rule relied
on. Particular attention is requested for Rule 18's practical calculation,
con-arb/probation cases, s191(12) elections, and the relationship between an
early certificate and the end of the 30-day conciliation period.

Overall result: ☐ Approved  ☐ Approved with corrections  ☐ Not approved

Signature: ____________________
