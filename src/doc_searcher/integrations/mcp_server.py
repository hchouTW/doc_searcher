# Purpose: Model Context Protocol (MCP) server exposing DocSearcher's index to AI clients
#          (Claude Code, Claude Desktop, MCP Inspector, ...).
# What the code does:
#   - Tools: search_documents, get_index_status, reindex_directory.
#   - Resource template docsearcher://document/{path}: stored text of an indexed document
#     (path is percent-encoded and decoded by the SDK; search results include ready-made URIs).
#   - Delegates all logic to doc_searcher.search.search_service.SearchService; this file only maps it to MCP.
# Usage notes, dependencies, or assumptions:
#   - pip install '.[mcp]'   (mcp 2.x, Python >= 3.10)
#   - doc-searcher-mcp (or python -m doc_searcher.integrations.mcp_server) serves over stdio;
#     clients launch it, so it is not run by hand.
#   - Shares ~/.doc_searcher/index.db and config.json with the GUI (override with
#     DOC_SEARCHER_DATA_DIR). Search folders are managed in the GUI.

from typing import Annotated, Any, Literal, Optional

from mcp.server.mcpserver import MCPServer, ResourceSecurity
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from doc_searcher.config import ConfigError
from doc_searcher.diagnostics import configure_logging
from doc_searcher.search.search_service import DOCUMENT_URI_PREFIX, MAX_SEARCH_LIMIT, SearchService
from doc_searcher.storage.errors import StorageError
from doc_searcher.version import APP_VERSION

FormatGroup = Literal["pdf", "word", "excel", "ppt", "text"]


mcp = MCPServer(
    "DocSearcher",
    version=APP_VERSION,
    instructions=(
        "Full-text search over the user's local PDF, Word, Excel, PowerPoint and text files "
        "indexed by the DocSearcher desktop app. Use search_documents to find passages, then "
        "read a hit's resource_uri for the full document text."
    ),
)

_service: Optional[SearchService] = None


def get_service() -> SearchService:
    """Create the service lazily so importing this module has no side effects."""
    global _service
    if _service is None:
        try:
            _service = SearchService()
        except (ConfigError, StorageError) as exc:
            raise ToolError(str(exc)) from exc
    return _service


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def search_documents(
    query: Annotated[
        str,
        Field(
            description=(
                'Keywords (Chinese or English). Supports "exact phrase", AND / OR / NOT, and '
                "filename:<name> to match file names."
            )
        ),
    ],
    formats: Annotated[
        Optional[list[FormatGroup]],
        Field(
            description=(
                "Only return these format groups: pdf, word (.docx/.doc), excel (.xlsx/.xls), "
                "ppt (.pptx/.ppt), text (.txt/.md/.csv). Omit to search all."
            )
        ),
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=MAX_SEARCH_LIMIT, description="Maximum documents to return.")
    ] = 20,
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def get_index_status() -> dict[str, Any]:
    """Report index size, last update time, app version, configured search folders, and the
    state of the most recent reindex_directory run."""
    return get_service().index_status()


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)
)
def reindex_directory(
    directory: Annotated[
        Optional[str],
        Field(
            description=(
                "A configured search folder, or a subfolder of one, to rescan. Omit to rescan all "
                "configured folders. New folders must be added in the DocSearcher app."
            )
        ),
    ] = None,
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
    """Console-script entry point (doc-searcher-mcp): serve over stdio.

    stdout is the protocol stream: DocSearcher logs to stderr (configure_logging) and routes
    MuPDF's messages into logging, so sys.stdout is never swapped.
    """
    configure_logging()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
