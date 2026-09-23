"""Protocol-level tests for mcp_server.py (skipped when the optional mcp SDK is missing)."""

import json
import shutil
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp.server.mcpserver")  # mcp 2.x only

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).parent.parent))

import mcp_server
from core.config import AppConfig
from core.search_service import SearchService, document_uri

ROOT = Path(__file__).parent.parent
SAMPLE_DIR = Path(__file__).parent / "sample_files"


@pytest.fixture
def indexed_service(tmp_path, monkeypatch):
    """Point mcp_server at an isolated, fully indexed copy of the sample files."""
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(tmp_path / "data"))
    docs = tmp_path / "docs"
    shutil.copytree(SAMPLE_DIR, docs)
    config = AppConfig()
    config.directories = [str(docs)]
    config.save()
    service = SearchService(config)
    service.start_reindex()
    service.wait_for_reindex(60)
    monkeypatch.setattr(mcp_server, "_service", service)
    yield service
    service.db.close()


def run_client(scenario):
    async def main():
        async with Client(mcp_server.mcp) as client:
            return await scenario(client)
    return anyio.run(main)


def test_lists_tools_and_document_template(indexed_service):
    async def scenario(client):
        tools = await client.list_tools()
        templates = await client.list_resource_templates()
        return tools, templates

    tools, templates = run_client(scenario)
    by_name = {tool.name: tool for tool in tools.tools}
    assert set(by_name) == {"search_documents", "get_index_status", "reindex_directory"}
    schema = by_name["search_documents"].input_schema
    assert schema["required"] == ["query"]
    assert schema["properties"]["limit"]["maximum"] == 100
    assert by_name["search_documents"].annotations.read_only_hint is True
    assert [t.uri_template for t in templates.resource_templates] == ["docsearcher://document/{path}"]


def test_search_then_read_hit_as_resource(indexed_service):
    async def scenario(client):
        found = await client.call_tool("search_documents", {"query": "預算", "formats": ["pdf"]})
        uri = found.structured_content["results"][0]["resource_uri"]
        return found, await client.read_resource(uri)

    found, resource = run_client(scenario)
    assert not found.is_error
    hit = found.structured_content["results"][0]
    assert hit["file_type"] == "pdf" and hit["match_count"] >= 1
    text = resource.contents[0].text
    assert text.startswith("# " + hit["filename"]) and "預算" in text
    assert resource.contents[0].mime_type == "text/markdown"


def test_invalid_input_is_reported_as_tool_error(indexed_service):
    async def scenario(client):
        return [
            await client.call_tool("search_documents", {"query": "預算", "formats": ["images"]}),
            await client.call_tool("search_documents", {"query": "filename:"}),
            await client.call_tool("reindex_directory", {"directory": "/definitely/not/configured"}),
        ]

    bad_format, bad_query, bad_dir = run_client(scenario)
    assert all(result.is_error for result in (bad_format, bad_query, bad_dir))
    assert "not inside a configured search folder" in bad_dir.content[0].text


def test_unindexed_document_resource_is_not_found(indexed_service):
    async def scenario(client):
        with pytest.raises(Exception, match="not in the index"):
            await client.read_resource(document_uri(str(ROOT / "mcp_server.py")))

    run_client(scenario)


def test_stdio_server_round_trip_survives_stray_prints(tmp_path):
    """Launch the real server; a broken config.json makes AppConfig print to stdout."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "config.json").write_text("{not json", encoding="utf-8")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "mcp_server.py")],
        env={"DOC_SEARCHER_DATA_DIR": str(data_dir)},
        cwd=str(tmp_path),
    )

    errlog_path = tmp_path / "server_stderr.log"

    async def main():
        with open(errlog_path, "w", encoding="utf-8") as errlog:
            async with Client(stdio_client(params, errlog=errlog), read_timeout_seconds=60) as client:
                return await client.call_tool("get_index_status", {})

    status = anyio.run(main)

    # The print really happened (diverted to the child's stderr) and the protocol survived it.
    assert "[Config] Failed to load config" in errlog_path.read_text(encoding="utf-8")
    assert not status.is_error
    assert status.structured_content["total_documents"] == 0
    assert status.structured_content["index_path"] == str(data_dir / "index.db")
    json.dumps(status.structured_content)  # JSON-ready payload
