"""Unfair-dismissal ontology, structured on the Labour Relations Act.

The tag vocabulary follows the LRA and the Code of Good Practice, not a blog
taxonomy. Each tag carries the regular expressions that justify it, so every
applied tag can be explained by the exact text that triggered it.

The process-defect layer is written as employee-side counter-checks: these are
the failure modes a dismissed employee or their representative should test a
dismissal against. They are deliberately not framed as employer guidance.

Nothing here is legal advice. A tag records that language appeared in a
document; it does not decide whether a dismissal was fair.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Tag:
    """One ontology tag and the patterns that evidence it."""

    name: str
    category: str
    patterns: tuple[str, ...]
    description: str = ""
    lra_reference: str = ""
    # Some tags need two different signals before they are credible.
    require_all: bool = False


CAT_STATUTE = "statute_section"
CAT_PATHWAY = "jurisdiction_path"
CAT_FAIRNESS = "fairness"
CAT_MISCONDUCT = "misconduct"
CAT_INCAPACITY = "incapacity"
CAT_OPERATIONAL = "operational"
CAT_PROCESS_DEFECT = "process_defect"
CAT_REMEDY = "remedy_signal"

# ---------------------------------------------------------------------------
# Statutory anchors
# ---------------------------------------------------------------------------
STATUTE_TAGS: tuple[Tag, ...] = (
    Tag("s186_dismissal_definition", CAT_STATUTE,
        (r"\bsection\s*186\b", r"\bs\.?\s*186\b", r"\bmeaning of dismissal\b"),
        "Definition of dismissal and residual unfair labour practice.", "LRA s186"),
    Tag("s186_1_e_constructive", CAT_STATUTE,
        (r"\bsection\s*186\s*\(\s*1\s*\)\s*\(\s*e\s*\)", r"\bconstructive(ly)?\s+dismiss"),
        "Constructive dismissal: resignation made intolerable by the employer.",
        "LRA s186(1)(e)"),
    Tag("s186_2_unfair_labour_practice", CAT_STATUTE,
        (r"\bsection\s*186\s*\(\s*2\s*\)", r"\bresidual unfair labour practice\b",
         r"\bunfair labour practice\b"),
        "Residual unfair labour practice; secondary to dismissal.", "LRA s186(2)"),
    Tag("s187_automatically_unfair", CAT_STATUTE,
        (r"\bsection\s*187\b", r"\bs\.?\s*187\b", r"\bautomatic(ally)?\s+unfair\b"),
        "Automatically unfair dismissal grounds.", "LRA s187"),
    Tag("s188_fair_reason_and_procedure", CAT_STATUTE,
        (r"\bsection\s*188\b", r"\bs\.?\s*188\b",
         r"\bfair reason\b.{0,60}\bfair procedure\b"),
        "Dismissal must be for a fair reason and follow a fair procedure.",
        "LRA s188"),
    Tag("s189_operational_requirements", CAT_STATUTE,
        (r"\bsection\s*189\b(?!\s*A)", r"\bs\.?\s*189\b(?!\s*A)",
         r"\boperational requirements\b", r"\bretrench"),
        "Dismissal for operational requirements; consultation duty.", "LRA s189"),
    Tag("s189A_large_scale_retrenchment", CAT_STATUTE,
        (r"\bsection\s*189\s*A\b", r"\bs\.?\s*189A\b",
         r"\bfacilitat(or|ion)\b.{0,40}\bretrench"),
        "Large-scale retrenchment with facilitation and strike/adjudication.",
        "LRA s189A"),
    Tag("s191_referral_and_condonation", CAT_STATUTE,
        (r"\bsection\s*191\b", r"\bs\.?\s*191\b",
         r"\breferr(al|ed)\b.{0,40}\b(CCMA|bargaining council)\b"),
        "Referral of a dismissal dispute to the CCMA or a bargaining council.",
        "LRA s191"),
    Tag("s193_194_remedies", CAT_STATUTE,
        (r"\bsection\s*19[34]\b", r"\bs\.?\s*19[34]\b",
         r"\bcompensation\b.{0,40}\bmonths(?:'|’)? remuneration\b"),
        "Remedies: reinstatement, re-employment, compensation and their limits.",
        "LRA s193-194"),
    Tag("s145_review", CAT_STATUTE,
        (r"\bsection\s*145\b", r"\bs\.?\s*145\b",
         r"\breview\b.{0,40}\barbitration award\b"),
        "Review of a CCMA arbitration award by the Labour Court.", "LRA s145"),
    Tag("s158_1_g_review", CAT_STATUTE,
        (r"\bsection\s*158\s*\(\s*1\s*\)\s*\(\s*g\s*\)",),
        "Review of other administrative action under the LRA.", "LRA s158(1)(g)"),
    Tag("s198_tes_labour_broker", CAT_STATUTE,
        (r"\bsection\s*198[A-D]?\b", r"\bs\.?\s*198[A-D]?\b",
         r"\btemporary employment service\b", r"\blabour broker\b", r"\bTES\b"),
        "Temporary employment services, deemed employment and equal treatment.",
        "LRA s198-198D"),
    Tag("eea_discrimination", CAT_STATUTE,
        (r"\bEmployment Equity Act\b", r"\bEEA\b",
         r"\bunfair discrimination\b", r"\bsection\s*6\s*\(\s*1\s*\)"),
        "Employment Equity Act overlap: unfair discrimination in employment.",
        "EEA 55 of 1998"),
    Tag("protected_disclosure", CAT_STATUTE,
        (r"\bprotected disclosure\b", r"\bwhistle ?blow", r"\bPDA\b",
         r"\bProtected Disclosures Act\b"),
        "Protected disclosure / whistleblowing dismissal.",
        "Protected Disclosures Act 26 of 2000; LRA s187(1)(h)"),
)

# ---------------------------------------------------------------------------
# Dispute pathway
# ---------------------------------------------------------------------------
PATHWAY_TAGS: tuple[Tag, ...] = (
    Tag("internal_hearing", CAT_PATHWAY,
        (r"\bdisciplinary (hearing|enquiry|inquiry)\b", r"\binternal (hearing|appeal)\b",
         r"\bchairperson of the (hearing|enquiry|inquiry)\b"),
        "Employer's internal disciplinary process."),
    Tag("ccma_arbitration", CAT_PATHWAY,
        (r"\bCCMA\b", r"\bcommissioner\b", r"\barbitration award\b",
         r"\bcon-?arb\b", r"\bconciliation\b"),
        "CCMA conciliation or arbitration."),
    Tag("bargaining_council", CAT_PATHWAY,
        (r"\bbargaining council\b", r"\bMEIBC\b", r"\bMIBCO\b", r"\bNBCRFLI\b",
         r"\bcouncil arbitrat"),
        "Bargaining council jurisdiction under s191."),
    Tag("labour_court_review", CAT_PATHWAY,
        (r"\bLabour Court\b", r"\breview application\b", r"\bset aside the award\b"),
        "Labour Court review or adjudication."),
    Tag("labour_appeal_court", CAT_PATHWAY,
        (r"\bLabour Appeal Court\b", r"\bLAC\b", r"\bappeal against the judgment\b"),
        "Labour Appeal Court."),
    Tag("apex_court", CAT_PATHWAY,
        (r"\bConstitutional Court\b", r"\bSupreme Court of Appeal\b",
         r"\bleave to appeal\b.{0,40}\bConstitutional Court\b"),
        "Constitutional Court or SCA."),
)

# ---------------------------------------------------------------------------
# Fairness split - always stored separately
# ---------------------------------------------------------------------------
FAIRNESS_TAGS: tuple[Tag, ...] = (
    Tag("substantive_fairness", CAT_FAIRNESS,
        (r"\bsubstantive(ly)? (un)?fair(ness)?\b", r"\bfair reason (for|to) dismiss",
         r"\bvalid and fair reason\b", r"\bsanction of dismissal\b.{0,40}\bappropriate\b"),
        "Whether there was a fair reason to dismiss."),
    Tag("procedural_fairness", CAT_FAIRNESS,
        (r"\bprocedural(ly)? (un)?fair(ness)?\b", r"\bfair procedure\b",
         r"\baudi alteram partem\b", r"\bopportunity to (be heard|state his|state her)\b"),
        "Whether a fair procedure was followed."),
    Tag("automatically_unfair", CAT_FAIRNESS,
        (r"\bautomatic(ally)? unfair\b", r"\bsection\s*187\b"),
        "Automatically unfair dismissal under s187.", "LRA s187"),
    Tag("constructive_dismissal", CAT_FAIRNESS,
        (r"\bconstructive(ly)? dismiss", r"\bintolerable\b.{0,60}\bcontinue(d)? employment\b",
         r"\bresign(ed|ation)\b.{0,60}\bintolerable\b"),
        "Constructive dismissal.", "LRA s186(1)(e)"),
    Tag("sidumo_reasonableness", CAT_FAIRNESS,
        (r"\bSidumo\b", r"\breasonable decision-?maker\b",
         r"\bdecision that a reasonable decision-?maker could not reach\b"),
        "The Sidumo review standard, distinct from an appeal."),
)

# ---------------------------------------------------------------------------
# Misconduct
# ---------------------------------------------------------------------------
MISCONDUCT_TAGS: tuple[Tag, ...] = (
    Tag("dishonesty", CAT_MISCONDUCT,
        (r"\bdishonest(y)?\b", r"\bfraud(ulent)?\b", r"\bmisrepresent",
         r"\bfalsif(y|ied|ication)\b", r"\bbreach of trust\b"), "Dishonesty."),
    Tag("theft", CAT_MISCONDUCT,
        (r"\btheft\b", r"\bstole(n)?\b", r"\bsteal(ing)?\b",
         r"\bmisappropriat"), "Theft or misappropriation."),
    Tag("assault", CAT_MISCONDUCT,
        (r"\bassault(ed|ing)?\b", r"\bphysical (violence|altercation)\b",
         r"\bfight(ing)?\b"), "Assault or violence."),
    Tag("insubordination", CAT_MISCONDUCT,
        (r"\binsubordinat", r"\bgross insolence\b", r"\brefus(ed|al) to obey\b",
         r"\bdefian(ce|t)\b.{0,40}\binstruction\b"), "Insubordination or insolence."),
    Tag("absenteeism", CAT_MISCONDUCT,
        (r"\babsenteeism\b", r"\babsent without (leave|permission)\b", r"\bAWOL\b",
         r"\bdesertion\b", r"\bunauthorised absence\b"), "Absence without leave."),
    Tag("intoxication", CAT_MISCONDUCT,
        (r"\bintoxicat", r"\bunder the influence\b", r"\balcohol\b",
         r"\bbreathaly[sz]er\b", r"\bdrug test\b", r"\bcannabis\b"),
        "Intoxication or substance use."),
    Tag("sexual_harassment", CAT_MISCONDUCT,
        (r"\bsexual harassment\b", r"\bsexual(ly)? (assault|advance|misconduct)\b",
         r"\bunwelcome sexual\b"), "Sexual harassment."),
    Tag("racism", CAT_MISCONDUCT,
        (r"\bracis(m|t)\b", r"\bracial (slur|abuse|epithet)\b", r"\bk[a-z]{4}r\b",
         r"\bhate speech\b", r"\bderogatory racial\b"), "Racist conduct or speech."),
    Tag("social_media", CAT_MISCONDUCT,
        (r"\bsocial media\b", r"\bFacebook\b", r"\bWhatsApp\b", r"\bTwitter\b",
         r"\bTikTok\b", r"\bbring(ing)? the (company|employer) into disrepute\b"),
        "Social-media related misconduct."),
    Tag("conflict_of_interest", CAT_MISCONDUCT,
        (r"\bconflict of interest\b", r"\bundeclared interest\b",
         r"\bcompeting business\b", r"\bkickback\b", r"\bbrib"),
        "Conflict of interest or corruption."),
    Tag("safety_rule_breach", CAT_MISCONDUCT,
        (r"\bsafety (rule|procedure|standard|regulation)\b",
         r"\bzero tolerance\b", r"\bPPE\b", r"\blife-?saving rule\b",
         r"\bcardinal rule\b", r"\bsafety violation\b"),
        "Breach of a safety rule."),
    Tag("derivative_misconduct", CAT_MISCONDUCT,
        (r"\bderivative misconduct\b",
         r"\bfail(ed|ure) to (disclose|come forward)\b.{0,60}\b(knowledge|information)\b"),
        "Derivative misconduct: failure to disclose knowledge of wrongdoing."),
    Tag("unauthorised_possession", CAT_MISCONDUCT,
        (r"\bunauthorised possession\b", r"\bunlawful possession\b",
         r"\bpossession of (company|employer) property\b"), "Unauthorised possession."),
    Tag("poor_timekeeping", CAT_MISCONDUCT,
        (r"\bpoor timekeeping\b", r"\blate coming\b", r"\bhabitual(ly)? late\b",
         r"\btime ?keeping\b"), "Timekeeping."),
    Tag("gross_negligence", CAT_MISCONDUCT,
        (r"\bgross negligence\b", r"\bnegligen(t|ce)\b.{0,40}\bdut(y|ies)\b",
         r"\bdereliction of duty\b"), "Negligence."),
)

# ---------------------------------------------------------------------------
# Incapacity
# ---------------------------------------------------------------------------
INCAPACITY_TAGS: tuple[Tag, ...] = (
    Tag("poor_performance", CAT_INCAPACITY,
        (r"\bpoor (work )?performance\b", r"\bperformance (improvement|counselling)\b",
         r"\bPIP\b", r"\bsub-?standard performance\b", r"\bincapacity\b.{0,30}\bperformance\b"),
        "Incapacity: poor performance."),
    Tag("ill_health", CAT_INCAPACITY,
        (r"\bill(-| )health\b", r"\bincapacity\b.{0,30}\bill(ness)?\b",
         r"\bmedical boarding\b", r"\bchronic illness\b", r"\bmedically unfit\b"),
        "Incapacity: ill health."),
    Tag("incompatibility", CAT_INCAPACITY,
        (r"\bincompatibilit(y|ies)\b", r"\bbreakdown (in|of) (the )?(trust )?relationship\b",
         r"\birretrievable breakdown\b"), "Incapacity: incompatibility."),
    Tag("injury_on_duty", CAT_INCAPACITY,
        (r"\binjury on duty\b", r"\bIOD\b", r"\bCOIDA\b",
         r"\bCompensation for Occupational Injuries\b"), "Injury on duty / COIDA."),
)

# ---------------------------------------------------------------------------
# Operational requirements
# ---------------------------------------------------------------------------
OPERATIONAL_TAGS: tuple[Tag, ...] = (
    Tag("closure", CAT_OPERATIONAL,
        (r"\bclosure of\b", r"\bceased (operations|trading)\b", r"\bliquidat",
         r"\bbusiness rescue\b", r"\bshut ?down\b"), "Closure or insolvency."),
    Tag("restructure", CAT_OPERATIONAL,
        (r"\brestructur", r"\breorganis(e|ation)\b", r"\bnew operating model\b",
         r"\bredesign of (the )?structure\b"), "Restructuring."),
    Tag("technology_change", CAT_OPERATIONAL,
        (r"\bautomat(ion|ed)\b", r"\bmechanis(ation|ed)\b", r"\bnew technology\b",
         r"\bdigitali[sz]ation\b"), "Technological change."),
    Tag("contractor_replacement", CAT_OPERATIONAL,
        (r"\boutsourc", r"\bcontractor(s)? (took over|replaced)\b",
         r"\bsection\s*197\b", r"\btransfer of (a )?business\b"),
        "Outsourcing or transfer of business."),
    Tag("tender_loss", CAT_OPERATIONAL,
        (r"\blost the tender\b", r"\btender (was )?not renewed\b",
         r"\bcontract (was )?terminated by the client\b", r"\bloss of (the )?contract\b"),
        "Loss of a client contract or tender."),
    Tag("seasonal", CAT_OPERATIONAL,
        (r"\bseasonal\b", r"\bfixed-?term\b.{0,40}\bexpir", r"\bproject came to an end\b"),
        "Seasonal or fixed-term expiry."),
    Tag("commodity_downturn", CAT_OPERATIONAL,
        (r"\bcommodity price", r"\bfalling (gold|platinum|coal) price",
         r"\bunprofitable shaft\b", r"\bcare and maintenance\b"),
        "Commodity downturn (common driver of mining retrenchment)."),
)

# ---------------------------------------------------------------------------
# Process defects - employee-side counter-checks
# ---------------------------------------------------------------------------
PROCESS_DEFECT_TAGS: tuple[Tag, ...] = (
    Tag("defective_charge_sheet", CAT_PROCESS_DEFECT,
        (r"\b(defective|vague|no) charge sheet\b", r"\bcharges? (were|was) (vague|defective)\b",
         r"\bnot (properly )?charged\b", r"\bchanged the charge\b",
         r"\bdifferent charge\b.{0,40}\bfound guilty\b", r"\bmoving target\b"),
        "No or defective charge sheet, or charges that shifted."),
    Tag("chairperson_not_impartial", CAT_PROCESS_DEFECT,
        (r"\bbias(ed)?\b.{0,40}\bchairperson\b", r"\bchairperson\b.{0,40}\bbias(ed)?\b",
         r"\bprosecutor and (the )?chairperson\b", r"\bjudge in (his|her|its) own cause\b",
         r"\bnemo iudex\b"),
        "Chairperson not impartial, or prosecutor and chair fused."),
    Tag("representation_denied", CAT_PROCESS_DEFECT,
        (r"\bdenied (legal )?representation\b", r"\brefused (a )?representative\b",
         r"\bwithout (a )?(union )?representative\b", r"\bno interpreter\b",
         r"\bdenied an interpreter\b", r"\blanguage (barrier|difficulty)\b"),
        "Representation or interpretation denied."),
    Tag("evidence_not_disclosed", CAT_PROCESS_DEFECT,
        (r"\bnot disclosed\b.{0,40}\bevidence\b", r"\bevidence\b.{0,40}\bnot disclosed\b",
         r"\bdenied access to\b.{0,40}\b(docket|documents|statements)\b",
         r"\bwithheld (the )?evidence\b"),
        "Evidence not disclosed before the hearing."),
    Tag("predetermined_outcome", CAT_PROCESS_DEFECT,
        (r"\bpre-?determined\b", r"\bforegone conclusion\b", r"\bsham (hearing|process)\b",
         r"\brubber ?stamp\b", r"\bdecision (had )?already (been )?(taken|made)\b"),
        "Predetermined outcome; the hearing was a formality."),
    Tag("inconsistency", CAT_PROCESS_DEFECT,
        (r"\binconsisten(t|cy)\b", r"\bparity principle\b",
         r"\bsimilarly placed employees?\b", r"\bother employees? (were )?not dismissed\b",
         r"\bselective (discipline|prosecution)\b"),
        "Inconsistent discipline; comparable employees treated differently."),
    Tag("no_progressive_discipline", CAT_PROCESS_DEFECT,
        (r"\bprogressive discipline\b", r"\bcorrective discipline\b",
         r"\bsanction short of dismissal\b", r"\bfinal written warning\b.{0,60}\bappropriate\b",
         r"\blesser sanction\b"),
        "No consideration of a sanction short of dismissal."),
    Tag("service_and_record_ignored", CAT_PROCESS_DEFECT,
        (r"\blength of service\b", r"\bclean (disciplinary )?record\b",
         r"\byears of (unblemished )?service\b", r"\bpersonal circumstances\b",
         r"\bmitigat(ing|ion)\b"),
        "Length of service, clean record or mitigation not weighed."),
    Tag("zero_tolerance_without_proportionality", CAT_PROCESS_DEFECT,
        (r"\bzero tolerance\b", r"\bautomatic(ally)? dismiss",
         r"\bno discretion\b", r"\bblanket (rule|policy)\b"),
        "Zero-tolerance applied without a proportionality analysis."),
    Tag("paper_consultation_s189", CAT_PROCESS_DEFECT,
        (r"\bmeaningful (joint )?consensus", r"\bjoint consensus-?seeking\b",
         r"\bconsultation was (a )?(sham|formality|paper)\b",
         r"\bno (meaningful|genuine) consultation\b",
         r"\bfait accompli\b"),
        "s189 consultation was a paper exercise, not joint consensus-seeking."),
    Tag("selection_criteria_unfair", CAT_PROCESS_DEFECT,
        (r"\bselection criteria\b", r"\bLIFO\b", r"\blast in,? first out\b",
         r"\bfair and objective criteria\b", r"\bcriteria (were|was) (not )?(fair|objective)\b"),
        "Selection criteria not fair and objective."),
    Tag("alternatives_not_considered", CAT_PROCESS_DEFECT,
        (r"\bbumping\b", r"\balternative(s)? to (retrenchment|dismissal)\b",
         r"\bsuitable alternative (employment|position)\b", r"\bvacan(t|cies)\b",
         r"\bredeploy"),
        "Alternatives, vacancies or bumping not considered."),
    Tag("forced_resignation", CAT_PROCESS_DEFECT,
        (r"\bforced to resign\b", r"\bresign or be dismissed\b",
         r"\bmutual (separation|agreement)\b", r"\bcoerced\b.{0,40}\bresign",
         r"\bultimatum\b.{0,40}\bresign"),
        "Resignation obtained under pressure and packaged as mutual."),
    Tag("after_acquired_evidence", CAT_PROCESS_DEFECT,
        (r"\bafter-?acquired evidence\b", r"\bdiscovered after the dismissal\b",
         r"\bsubsequently discovered\b.{0,40}\bmisconduct\b"),
        "After-acquired evidence used to justify a dismissal already taken."),
    Tag("condonation_late_referral", CAT_PROCESS_DEFECT,
        (r"\bcondonation\b", r"\blate referral\b", r"\bout of time\b",
         r"\bdegree of lateness\b", r"\bprospects of success\b"),
        "Condonation or late-referral issue."),
    Tag("review_test_confusion", CAT_PROCESS_DEFECT,
        (r"\breview is not an appeal\b", r"\bconflat(ed|ing)\b.{0,40}\bappeal\b",
         r"\bgross irregularity\b", r"\bexceeded (his|her|its) powers\b",
         r"\bmisconceived the (nature of the )?enquiry\b"),
        "Review standard confused with an appeal."),
    Tag("no_hearing_at_all", CAT_PROCESS_DEFECT,
        (r"\bno (disciplinary )?hearing (was )?(held|convened)\b",
         r"\bsummar(y|ily) dismiss", r"\bdismissed on the spot\b",
         r"\bwithout (any )?hearing\b"),
        "No hearing was held at all."),
    Tag("precautionary_suspension_abuse", CAT_PROCESS_DEFECT,
        (r"\bprecautionary suspension\b", r"\bsuspended (with|without) pay\b",
         r"\bprolonged suspension\b"),
        "Precautionary suspension issues."),
)

# ---------------------------------------------------------------------------
# Remedy signals
# ---------------------------------------------------------------------------
REMEDY_TAGS: tuple[Tag, ...] = (
    Tag("reinstatement", CAT_REMEDY,
        (r"\breinstat(e|ed|ement)\b",), "Reinstatement sought or ordered."),
    Tag("reemployment", CAT_REMEDY,
        (r"\bre-?employ(ed|ment)\b",), "Re-employment sought or ordered."),
    Tag("compensation", CAT_REMEDY,
        (r"\bcompensation\b", r"\bsolatium\b",
         r"\b(\d{1,2})\s*months(?:'|’)?\s*(remuneration|salary|compensation)\b"),
        "Compensation sought or ordered."),
    Tag("costs", CAT_REMEDY,
        (r"\bno order as to costs\b", r"\bcosts to follow\b", r"\border of costs\b"),
        "Costs."),
)

ALL_TAGS: tuple[Tag, ...] = (
    STATUTE_TAGS + PATHWAY_TAGS + FAIRNESS_TAGS + MISCONDUCT_TAGS
    + INCAPACITY_TAGS + OPERATIONAL_TAGS + PROCESS_DEFECT_TAGS + REMEDY_TAGS
)

CATEGORIES: tuple[str, ...] = (
    CAT_STATUTE, CAT_PATHWAY, CAT_FAIRNESS, CAT_MISCONDUCT,
    CAT_INCAPACITY, CAT_OPERATIONAL, CAT_PROCESS_DEFECT, CAT_REMEDY,
)


@dataclass(slots=True)
class OntologySchema:
    """Serialisable description of the ontology for ontology/*.json."""

    version: str = "1.0"
    categories: tuple[str, ...] = CATEGORIES
    tags: tuple[Tag, ...] = field(default=ALL_TAGS)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "jurisdiction": "ZA",
            "statute_basis": "Labour Relations Act 66 of 1995 and related instruments",
            "disclaimer": (
                "These tags record that particular language appeared in a document. "
                "They are not legal conclusions and are not legal advice. Unfair-"
                "dismissal outcomes turn on facts, condonation, forum rules and "
                "representation."
            ),
            "categories": list(self.categories),
            "tags": [
                {
                    "name": tag.name,
                    "category": tag.category,
                    "description": tag.description,
                    "lra_reference": tag.lra_reference,
                    "patterns": list(tag.patterns),
                }
                for tag in self.tags
            ],
        }
