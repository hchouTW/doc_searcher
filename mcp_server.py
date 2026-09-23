# Purpose: Model Context Protocol (MCP) server exposing DocSearcher's index to AI clients
#          (Claude Code, Claude Desktop, MCP Inspector, ...).
# What the code does:
#   - Tools: search_documents, get_index_status, reindex_directory.
#   - Resource template docsearcher://document/{path}: stored text of an indexed document
#     (path is percent-encoded and decoded by the SDK; search results include ready-made URIs).
#   - Delegates all logic to core.search_service.SearchService; this file only maps it to MCP.
# Usage notes, dependencies, or assumptions:
#   - pip install -r requirements-mcp.txt   (mcp 2.x, Python >= 3.10)
#   - python mcp_server.py (or the doc-searcher-mcp console script) serves over stdio;
#     clients launch it, so it is not run by hand.
#   - Shares ~/.doc_searcher/index.db and config.json with the GUI (override with
#     DOC_SEARCHER_DATA_DIR). Search folders are managed in the GUI.

import sys
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Literal, Optional

from mcp.server.mcpserver import MCPServer, ResourceSecurity
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from core.search_service import DOCUMENT_URI_PREFIX, MAX_SEARCH_LIMIT, SearchService
from core.version import APP_VERSION

FormatGroup = Literal["pdf", "word", "excel", "ppt", "text"]


@asynccontextmanager
async def _stdout_to_stderr(_server: MCPServer) -> AsyncIterator[None]:
    """Send stray print() output to stderr while serving.

    The core modules print diagnostics. The SDK diverts fd 1, but sys.stdout's block buffer can
    still flush onto the stdio wire after the fd is restored. The lifespan runs after the
    transport has taken its private copy of stdout, so swapping sys.stdout here is safe.
    """
    original = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = original


mcp = MCPServer(
    "DocSearcher",
    version=APP_VERSION,
    instructions=(
        "Full-text search over the user's local PDF, Word, Excel, PowerPoint and text files "
        "indexed by the DocSearcher desktop app. Use search_documents to find passages, then "
        "read a hit's resource_uri for the full document text."
    ),
    lifespan=_stdout_to_stderr,
)

_service: Optional[SearchService] = None


def get_service() -> SearchService:
    """Create the service lazily so importing this module has no side effects."""
    global _service
    if _service is None:
        _service = SearchService()
    return _service


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def search_documents(
    query: Annotated[str, Field(description=(
        'Keywords (Chinese or English). Supports "exact phrase", AND / OR / NOT, and '
        "filename:<name> to match file names."
    ))],
    formats: Annotated[Optional[list[FormatGroup]], Field(description=(
        "Only return these format groups: pdf, word (.docx/.doc), excel (.xlsx/.xls), "
        "ppt (.pptx/.ppt), text (.txt/.md/.csv). Omit to search all."
    ))] = None,
    limit: Annotated[int, Field(ge=1, le=MAX_SEARCH_LIMIT, description="Maximum documents to return.")] = 20,
) -> dict[str, Any]:
    """Search indexed local documents and return ranked hits with highlighted snippets.

    Each result has the file path, type, size, modified time, match_count (number of matching
    pages/sheets/slides/sections), per-location snippets with hits in **bold**, and a
    resource_uri for reading the full text.
    """
    try:
        return get_service().search(query, formats, limit)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def get_index_status() -> dict[str, Any]:
    """Report index size, last update time, app version, configured search folders, and the
    state of the most recent reindex_directory run."""
    return get_service().index_status()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def reindex_directory(
    directory: Annotated[Optional[str], Field(description=(
        "A configured search folder, or a subfolder of one, to rescan. Omit to rescan all "
        "configured folders. New folders must be added in the DocSearcher app."
    ))] = None,
) -> dict[str, Any]:
    """Start an incremental background re-index (new, changed and deleted files) and return
    immediately. Poll get_index_status to see when it finishes and what changed."""
    try:
        return get_service().start_reindex(directory)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


# Absolute paths are expected here; safety comes from only serving documents already in the
# index (text from index.db, never read from disk), so the SDK's path checks are exempted.
@mcp.resource(
    DOCUMENT_URI_PREFIX + "{path}",
    name="indexed_document",
    description="Extracted text of an indexed document, split by page/sheet/slide/section.",
    mime_type="text/markdown",
    security=ResourceSecurity(exempt_params={"path"}),
)
def read_document(path: str) -> str:
    try:
        return get_service().document_text(path)
    except LookupError as exc:
        raise ResourceNotFoundError(str(exc)) from exc


def main() -> None:
    """Console-script entry point (doc-searcher-mcp): serve over stdio."""
    mcp.run("stdio")


if __name__ == "__main__":
    main()
