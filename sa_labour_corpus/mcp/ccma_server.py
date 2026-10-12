"""Start the local CCMA MCP server (stdio, read-only).

    python mcp/ccma_server.py [--root PATH]

Equivalent to `labour-corpus ccma mcp`. Uses only the standard library, so it
runs without the harvester's third-party dependencies installed. The catalogue
defaults to data/ccma/catalogue.sqlite under the project root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from labour_corpus.ccma.mcp_server import serve  # noqa: E402  (path set above)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT,
                        help="project root holding data/ccma/catalogue.sqlite")
    args = parser.parse_args()
    serve(args.root.resolve() / "data" / "ccma" / "catalogue.sqlite")


if __name__ == "__main__":
    main()
