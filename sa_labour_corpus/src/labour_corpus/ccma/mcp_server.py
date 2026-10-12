"""A local, read-only MCP server over the CCMA catalogue. Standard library only.

Speaks MCP's JSON-RPC 2.0 over stdio (one message per line). Every tool reads
the local catalogue; none reaches the network. The single exception,
``ccma_refresh``, refuses unless the caller passes ``allow_live_harvest=true``
*and* the operator has enabled it in the server's environment
(``CCMA_MCP_ALLOW_LIVE_HARVEST=1``) -- off by default -- and even then runs the
ordinary harvester with every source gate intact and at most 5 records.

stdout carries protocol messages only; diagnostics go to stderr.
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, TextIO
from collections.abc import Callable

from . import query

SERVER_NAME = "ccma-corpus"
SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
LIVE_HARVEST_ENV = "CCMA_MCP_ALLOW_LIVE_HARVEST"
MAX_REFRESH_LIMIT = 5

INSTRUCTIONS = (
    "Read-only access to a local catalogue of published CCMA awards, CCMA practice material "
    "and Labour Court judgments that review CCMA awards (marked related_authority -- these "
    "are not awards). Knowledge units are rule-derived research notes, each citing catalogue "
    "records. Not legal advice; verify against the full award and current CCMA Rules. The "
    "catalogue is never a complete record of CCMA decisions -- call ccma_coverage for scope."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "ccma_search",
        "description": "Full-text search of catalogued records (redacted text). Returns metadata "
                       "and a snippet per hit.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to match (all must appear)."},
                "year_from": {"type": "integer"},
                "year_to": {"type": "integer"},
                "dispute_type": {"type": "string",
                                 "description": "e.g. dismissal_misconduct; see ccma_scenarios."},
                "limit": {"type": "integer", "minimum": 1, "maximum": query.MAX_LIMIT},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "ccma_get",
        "description": "One record by native id, 'source:native_id', case number (GAJB1234-24) "
                       "or neutral citation, with its case card, strategies and scenarios.",
        "inputSchema": {
            "type": "object",
            "properties": {"native_id_or_case_number": {"type": "string"}},
            "required": ["native_id_or_case_number"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "ccma_strategies",
        "description": "Research notes for one record. Every note cites a catalogue native_id "
                       "and carries a confidence label.",
        "inputSchema": {
            "type": "object",
            "properties": {"native_id_or_case_number": {"type": "string"}},
            "required": ["native_id_or_case_number"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "ccma_scenarios",
        "description": "Scenarios (fact patterns shared by two or more catalogued records) for a "
                       "dispute type, with the outcome range observed in those records only.",
        "inputSchema": {
            "type": "object",
            "properties": {"dispute_type": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "ccma_coverage",
        "description": "What the catalogue holds, by source and status, and what it does not. "
                       "completeness_claim is always false.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "ccma_refresh",
        "description": "Run a small live harvest (max 5 records per source). Refused unless "
                       "allow_live_harvest is true AND the operator enabled live harvest for "
                       "this server. Off by default.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "allow_live_harvest": {"type": "boolean"},
                "source": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_REFRESH_LIMIT},
            },
            "required": ["allow_live_harvest"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "openWorldHint": True},
    },
]


class ToolError(Exception):
    """Reported to the client as a tool result with isError = true."""


class Server:
    # Overridable so another read-only server can reuse this protocol handling.
    name = SERVER_NAME
    version = SERVER_VERSION
    instructions = INSTRUCTIONS
    tools = TOOLS

    def __init__(self, catalogue_path: Path, environ: dict[str, str] | None = None) -> None:
        self.catalogue_path = Path(catalogue_path)
        self.environ = dict(os.environ if environ is None else environ)

    # -- tools -------------------------------------------------------------
    def _conn(self):
        try:
            return query.open_catalogue(self.catalogue_path)
        except query.QueryError as exc:
            raise ToolError(str(exc)) from exc

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "ccma_search": self._search,
            "ccma_get": lambda a: self._with_conn(query.get, a["native_id_or_case_number"]),
            "ccma_strategies": lambda a: self._with_conn(query.strategies, a["native_id_or_case_number"]),
            "ccma_scenarios": lambda a: self._with_conn(query.scenarios, a.get("dispute_type")),
            "ccma_coverage": lambda a: self._with_conn(query.coverage),
            "ccma_refresh": self._refresh,
        }
        if name not in handlers:
            raise ToolError(f"unknown tool {name!r}")
        try:
            return handlers[name](args)
        except KeyError as exc:
            raise ToolError(f"missing argument {exc.args[0]!r}") from exc
        except query.QueryError as exc:
            raise ToolError(str(exc)) from exc

    def _with_conn(self, fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        conn = self._conn()
        try:
            return fn(conn, *args)
        finally:
            conn.close()

    def _search(self, a: dict[str, Any]) -> dict[str, Any]:
        return self._with_conn(query.search, a["query"], a.get("year_from"), a.get("year_to"),
                               a.get("dispute_type"), a.get("limit", 10))

    def live_harvest_enabled(self) -> bool:
        return self.environ.get(LIVE_HARVEST_ENV, "") == "1"

    def _refresh(self, a: dict[str, Any]) -> dict[str, Any]:
        if a.get("allow_live_harvest") is not True:
            return {"refused": True, "reason": "allow_live_harvest was not set to true"}
        if not self.live_harvest_enabled():
            return {"refused": True,
                    "reason": f"live harvest is disabled for this server ({LIVE_HARVEST_ENV} != 1); "
                              "the operator must enable it"}
        from .allowlist import load_allowlist          # heavy imports only when permitted
        from .harvest import HarvestOptions, Harvester

        email = self.environ.get("CCMA_CONTACT_EMAIL")
        if not email:
            return {"refused": True, "reason": "CCMA_CONTACT_EMAIL is not set for this server"}
        limit = max(1, min(int(a.get("limit", MAX_REFRESH_LIMIT)), MAX_REFRESH_LIMIT))
        options = HarvestOptions(
            root=self.catalogue_path.parents[2],
            source_ids=[a["source"]] if a.get("source") else [],
            limit=limit, allow_network=True,
            popia_ack=self.environ.get("CCMA_POPIA_ACK", "") == "1",
            contact_email=email,
        )
        result = Harvester(load_allowlist(), options,
                           out=lambda line: print(line, file=sys.stderr)).run()
        return {
            "run_id": result.run_id,
            "sources": [{"source": s.source_id, "attempted": s.attempted, "refusal": s.refusal,
                         "found": s.found, "seeded": s.seeded, "failed": s.failed}
                        for s in result.sources],
            "report": str(result.report_path),
            "completeness_claim": False,
        }

    # -- protocol ----------------------------------------------------------
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message
        params = message.get("params") or {}

        if not isinstance(method, str):
            return None if is_notification else _error(msg_id, -32600, "invalid request")
        if method.startswith("notifications/"):
            return None
        if not isinstance(params, dict):
            return None if is_notification else _error(msg_id, -32602, "invalid params")
        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
                result: dict[str, Any] = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                    "instructions": self.instructions,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools}
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                try:
                    if not isinstance(name, str) or not isinstance(args, dict):
                        raise ToolError("tools/call needs a string 'name' and an object 'arguments'")
                    payload = self.call_tool(name, args)
                    result = {
                        "content": [{"type": "text",
                                     "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                        "structuredContent": payload,
                        "isError": False,
                    }
                except ToolError as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            elif method in {"resources/list", "prompts/list"}:
                result = {method.split("/")[0]: []}
            else:
                return None if is_notification else _error(msg_id, -32601, f"method not found: {method}")
        except Exception as exc:  # noqa: BLE001 -- the server must answer, not die
            traceback.print_exc(file=sys.stderr)
            return None if is_notification else _error(msg_id, -32603, f"internal error: {type(exc).__name__}")
        return None if is_notification else {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _utf8(stream: TextIO) -> TextIO:
    """Put a real stdio stream on UTF-8, which MCP's stdio transport requires.

    A piped stream otherwise inherits the platform code page -- cp1252 on
    Windows -- which writes replies a client cannot decode, fails outright on
    characters outside it, and reads a client's UTF-8 as mojibake, so a search
    for a non-ASCII term silently misses.
    """
    if isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(encoding="utf-8", errors="replace")
    return stream


def serve(catalogue_path: Path, stdin: TextIO | None = None, stdout: TextIO | None = None,
          server: Server | None = None) -> None:
    stdin = stdin or _utf8(sys.stdin)
    stdout = stdout or _utf8(sys.stdout)
    server = server or Server(catalogue_path)
    print(f"{server.name}: serving {catalogue_path} (read-only)", file=sys.stderr)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            responses: list[dict[str, Any]] = [_error(None, -32700, "parse error")]
        else:
            batch = message if isinstance(message, list) else [message]
            responses = [r for r in (server.handle(m) if isinstance(m, dict)
                                     else _error(None, -32600, "invalid request") for m in batch)
                         if r is not None]
        for response in responses:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
