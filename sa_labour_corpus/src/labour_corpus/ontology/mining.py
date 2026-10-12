"""Mining-labour overlay.

Mining dismissals sit on top of ordinary LRA analysis, not beside it. The
Mine Health and Safety Act imposes duties that employers frequently invoke to
justify zero-tolerance safety dismissals, and the Labour Court has repeatedly
held that a safety rule breach still has to be tested for proportionality.

This module recognises when a document is a mining-sector matter and which
mining-specific instruments and hazards it engages.
"""

from __future__ import annotations

from .dismissal import Tag

CAT_MINING_STATUTE = "mining_statute"
CAT_MINING_CONTEXT = "mining_context"
CAT_MINING_EMPLOYER = "mining_employer"
CAT_MINING_HAZARD = "mining_hazard"

MINING_STATUTE_TAGS: tuple[Tag, ...] = (
    Tag("mhsa", CAT_MINING_STATUTE,
        (r"\bMine Health and Safety Act\b", r"\bMHSA\b",
         r"\bAct 29 of 1996\b"),
        "Mine Health and Safety Act 29 of 1996.", "MHSA 29 of 1996"),
    Tag("mhsa_s23_right_to_refuse", CAT_MINING_STATUTE,
        (r"\bsection\s*23\b.{0,60}\b(MHSA|Mine Health)\b",
         r"\bright to refuse (to perform )?dangerous work\b",
         r"\bwithdraw(al)? from (a )?dangerous (working )?place\b"),
        "MHSA s23: employee right to refuse dangerous work.", "MHSA s23"),
    # "section 54" appears in many statutes, so a bare reference is not
    # evidence of the MHSA. Every alternative below requires mining context.
    Tag("mhsa_s54_stoppage", CAT_MINING_STATUTE,
        (r"\bsection\s*54\b.{0,120}\b(mine|mining|inspector of mines|MHSA)\b",
         r"\b(mine|mining|inspector of mines|MHSA)\b.{0,120}\bsection\s*54\b",
         r"\bsection\s*54\s*(notice|instruction|stoppage)\b",
         r"\bprincipal inspector of mines\b"),
        "MHSA s54: inspector's stoppage instruction.", "MHSA s54"),
    Tag("mhsa_s2_3_employer_duties", CAT_MINING_STATUTE,
        (r"\bsafe (working )?environment\b.{0,60}\bmine\b",
         r"\bhealth and safety (policy|representative|committee)\b",
         r"\bsection\s*[23]\b.{0,40}\bMHSA\b"),
        "MHSA employer duty to provide a safe mine.", "MHSA s2-3"),
    Tag("mprda", CAT_MINING_STATUTE,
        (r"\bMineral and Petroleum Resources Development Act\b", r"\bMPRDA\b",
         r"\bAct 28 of 2002\b", r"\bsocial and labour plan\b", r"\bSLP\b",
         r"\bMining Charter\b"),
        "MPRDA 28 of 2002, social and labour plans, Mining Charter.",
        "MPRDA 28 of 2002"),
    Tag("odmwa", CAT_MINING_STATUTE,
        (r"\bOccupational Diseases in Mines and Works Act\b", r"\bODMWA\b",
         r"\bsilicosis\b", r"\bpneumoconiosis\b"),
        "Occupational Diseases in Mines and Works Act 78 of 1973.",
        "ODMWA 78 of 1973"),
)

MINING_CONTEXT_TAGS: tuple[Tag, ...] = (
    Tag("underground_operations", CAT_MINING_CONTEXT,
        (r"\bunderground\b", r"\bstop(e|ing|er)\b", r"\bshaft\b", r"\badit\b",
         r"\bwinze\b", r"\bhaulage\b", r"\bdecline shaft\b", r"\bcage\b"),
        "Underground mining operations."),
    Tag("opencast_operations", CAT_MINING_CONTEXT,
        (r"\bopen ?cast\b", r"\bopen ?pit\b", r"\bstrip mining\b",
         r"\bdragline\b", r"\bhaul truck\b"), "Opencast operations."),
    Tag("mine_roles", CAT_MINING_CONTEXT,
        (r"\bmine overseer\b", r"\bshift (boss|supervisor)\b", r"\bminer\b",
         r"\brock drill operator\b", r"\bRDO\b", r"\bbanksman\b", r"\bonsetter\b",
         r"\bteam leader\b.{0,30}\b(crew|panel)\b", r"\bblaster\b",
         r"\bmine manager\b", r"\bsafety officer\b"), "Mining occupational roles."),
    Tag("commodity", CAT_MINING_CONTEXT,
        (r"\bgold mine\b", r"\bplatinum\b", r"\bPGM\b", r"\bcoal mine\b",
         r"\bchrome\b", r"\bmanganese\b", r"\bdiamond mine\b", r"\biron ore\b",
         r"\bcolliery\b"), "Commodity context."),
    Tag("mine_tes", CAT_MINING_CONTEXT,
        (r"\bcontractor\b.{0,40}\bmine\b", r"\bmine\b.{0,40}\bcontractor\b",
         r"\blabour broker\b.{0,60}\bmine\b", r"\bTES\b.{0,60}\bmine\b"),
        "Contractor or TES labour on a mine."),
)

# Employer names are used only to recognise sector context in published
# judgments. No identity enrichment beyond what the judgment already states.
MINING_EMPLOYER_TAGS: tuple[Tag, ...] = (
    Tag("major_mining_employer", CAT_MINING_EMPLOYER,
        (r"\bSibanye(-| )?Stillwater\b", r"\bHarmony Gold\b", r"\bAngloGold\b",
         r"\bAnglo American\b", r"\bImpala Platinum\b", r"\bImplats\b",
         r"\bNortham Platinum\b", r"\bLonmin\b", r"\bExxaro\b", r"\bGlencore\b",
         r"\bSeriti\b", r"\bThungela\b", r"\bKumba Iron Ore\b", r"\bGold Fields\b",
         r"\bDe Beers\b", r"\bPetra Diamonds\b", r"\bSamancor\b", r"\bAssmang\b",
         r"\bRoyal Bafokeng Platinum\b", r"\bVillage Main Reef\b",
         r"\bMurray (and|&) Roberts Cementation\b", r"\bJIC Mining\b",
         r"\bRedpath\b", r"\bMoolman Mining\b"),
        "A named mining employer appears in the document."),
)

MINING_HAZARD_TAGS: tuple[Tag, ...] = (
    Tag("fatality_or_serious_injury", CAT_MINING_HAZARD,
        (r"\bfatal(ity|ities)?\b", r"\bdeceased\b", r"\bfell of rock\b",
         r"\bfall of ground\b", r"\bFOG\b", r"\bserious injury\b",
         r"\bmine accident\b"), "Fatality or serious injury context."),
    Tag("explosives_and_blasting", CAT_MINING_HAZARD,
        (r"\bexplosive(s)?\b", r"\bblast(ing)?\b", r"\bmisfire\b",
         r"\bcharge up\b", r"\bdetonator\b"), "Explosives and blasting."),
    Tag("methane_and_gas", CAT_MINING_HAZARD,
        (r"\bmethane\b", r"\bgas (test|testing|reading)\b", r"\bflammable gas\b",
         r"\bCH4\b", r"\bexplosion\b"), "Gas hazard."),
    Tag("lock_out_isolation", CAT_MINING_HAZARD,
        (r"\block-?out\b", r"\bisolation (procedure|permit)\b",
         r"\bpermit to work\b", r"\btag ?out\b"), "Isolation and lock-out."),
    Tag("winder_and_conveyance", CAT_MINING_HAZARD,
        (r"\bwinder\b", r"\bconveyance\b", r"\brope\b.{0,30}\bshaft\b",
         r"\bman ?cage\b"), "Winder and shaft conveyance."),
)

ALL_MINING_TAGS: tuple[Tag, ...] = (
    MINING_STATUTE_TAGS + MINING_CONTEXT_TAGS
    + MINING_EMPLOYER_TAGS + MINING_HAZARD_TAGS
)

MINING_CATEGORIES: tuple[str, ...] = (
    CAT_MINING_STATUTE, CAT_MINING_CONTEXT,
    CAT_MINING_EMPLOYER, CAT_MINING_HAZARD,
)

# A document is called "mining" only when it hits at least this many distinct
# mining tags, which keeps a passing mention of the word "mine" from flipping
# the sector. The decision and its evidence are always written to the parsed
# JSON so the call can be audited.
MINING_TAG_THRESHOLD = 2


def overlay_to_dict() -> dict:
    return {
        "version": "1.0",
        "jurisdiction": "ZA",
        "purpose": (
            "Mining-labour overlay. Mining dismissals are analysed under the LRA "
            "with the Mine Health and Safety Act layered on top; a safety rule "
            "breach still requires a proportionality analysis."
        ),
        "sector_decision_rule": {
            "sector_tag": "mining",
            "min_distinct_tags": MINING_TAG_THRESHOLD,
            "note": (
                "Evidence for the mining sector call is always written to the "
                "parsed document so it can be audited."
            ),
        },
        "categories": list(MINING_CATEGORIES),
        "tags": [
            {
                "name": tag.name,
                "category": tag.category,
                "description": tag.description,
                "statute_reference": tag.lra_reference,
                "patterns": list(tag.patterns),
            }
            for tag in ALL_MINING_TAGS
        ],
    }
