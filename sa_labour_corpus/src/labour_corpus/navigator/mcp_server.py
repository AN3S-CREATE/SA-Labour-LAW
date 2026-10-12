"""MCP server for the Dismissal Navigator (stdio, read-only).

Reuses the CCMA server's JSON-RPC handling and exposes two tools:
``navigator_assess`` (facts -> grounded assessment) and
``navigator_provisions`` (grounding status of every quoted provision). Nothing
is written and nothing reaches the network. Facts are not logged.

    labour-corpus navigate mcp [--out data/corpus]
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..ccma.mcp_server import Server, ToolError
from .engine import Navigator
from .evidence import CorpusEvidence
from .grounding import CorpusSource, TextCache, corpus_source, ground
from .models import DISCLAIMER, CaseFacts
from .provisions import PROVISIONS
from .render import to_markdown
from ..config import CorpusPaths

NAVIGATOR_NAME = "dismissal-navigator"
NAVIGATOR_VERSION = "0.1.0"

NAVIGATOR_INSTRUCTIONS = (
    "Deadlines, forum routing and remedy ceilings for South African dismissal, unfair "
    "labour practice and workplace discrimination disputes, derived from the Labour "
    "Relations Act and Employment Equity Act. Every legal statement carries a provision "
    "whose verbatim wording is re-checked against statutes held in a local corpus; "
    "grounding 'not_found' means do not rely on that statement. Do not send names, ID "
    "numbers or contact details: the tools do not accept them. " + DISCLAIMER
)


def _assess_schema() -> dict[str, Any]:
    schema = CaseFacts.model_json_schema()
    schema["properties"]["as_of"] = {
        "type": "string", "format": "date",
        "description": "Assessment date, YYYY-MM-DD. Defaults to today."}
    schema["properties"]["include_evidence"] = {
        "type": "boolean", "description": "Also list held judgments and codes on the issue."}
    schema["additionalProperties"] = False
    schema.pop("title", None)
    return schema


NAVIGATOR_TOOLS: list[dict[str, Any]] = [
    {
        "name": "navigator_assess",
        "description": "Assess a dismissal, unfair labour practice or discrimination dispute "
                       "from stated facts: classification, referral deadlines, forum route, "
                       "remedy ceiling, onus and a process checklist, each tied to quoted "
                       "statute wording with its grounding status. Not legal advice.",
        "inputSchema": _assess_schema(),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "navigator_provisions",
        "description": "The grounding status of every statutory provision the navigator "
                       "relies on: verified, not_found or not_checked, with the held "
                       "document it was found in.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


class NavigatorServer(Server):
    name = NAVIGATOR_NAME
    version = NAVIGATOR_VERSION
    instructions = NAVIGATOR_INSTRUCTIONS
    tools = NAVIGATOR_TOOLS

    def __init__(self, corpus_root: Path, today: date | None = None) -> None:
        super().__init__(Path(corpus_root) / "db" / "corpus.sqlite", environ={})
        self.corpus_root = Path(corpus_root)
        self.today = today
        # Normalised Act texts, shared by every call. Each call still queries
        # which copies are held (D-018, D-022); only an unchanged file is not
        # read and normalised again.
        self._text_cache: TextCache = {}

    def _statutes(self) -> CorpusSource | None:
        return corpus_source(self.corpus_root, self._text_cache)

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "navigator_assess":
            return self._assess(dict(args))
        if name == "navigator_provisions":
            return self._provisions()
        raise ToolError(f"unknown tool {name!r}")

    def _assess(self, args: dict[str, Any]) -> dict[str, Any]:
        as_of = args.pop("as_of", None)
        evidence = bool(args.pop("include_evidence", False))
        try:
            facts = CaseFacts.model_validate(args)
            today = date.fromisoformat(as_of) if as_of else (self.today or date.today())
            finder = CorpusEvidence(CorpusPaths(self.corpus_root)) if evidence else None
            result = Navigator(self._statutes(), finder).assess(facts, today)
        except ValidationError as exc:
            raise ToolError("invalid facts: " + "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'facts'}: {e['msg']}"
                for e in exc.errors())) from exc
        except ValueError as exc:
            raise ToolError(f"invalid facts: {exc}") from exc
        payload = result.model_dump(mode="json")
        payload["fully_grounded"] = result.fully_grounded
        payload["markdown"] = to_markdown(result)
        return payload

    def _provisions(self) -> dict[str, Any]:
        source = self._statutes()
        refs = [ground(p, source).model_dump(mode="json") for p in PROVISIONS.values()]
        return {"provisions": refs,
                "all_verified": all(r["grounding"] == "verified" for r in refs)}
