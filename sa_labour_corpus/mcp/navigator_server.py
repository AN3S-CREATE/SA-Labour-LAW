"""Start the Dismissal Navigator MCP server (stdio, read-only).

    python mcp/navigator_server.py [--root PATH]

Equivalent to `labour-corpus navigate mcp`. Unlike the CCMA launcher this needs
the package's dependencies (pydantic, PyYAML) installed. The corpus defaults to
data/corpus under the project root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from labour_corpus.ccma.mcp_server import serve  # noqa: E402  (path set above)
from labour_corpus.navigator.mcp_server import NavigatorServer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT / "data" / "corpus",
                        help="corpus root holding db/corpus.sqlite")
    args = parser.parse_args()
    root = args.root.resolve()
    serve(root / "db" / "corpus.sqlite", server=NavigatorServer(root))


if __name__ == "__main__":
    main()
