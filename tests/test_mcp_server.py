"""Protocol-level tests for doc_searcher.integrations.mcp_server (skipped when the optional mcp SDK is missing)."""

import json
import shutil
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp.server.mcpserver")  # mcp 2.x only

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from doc_searcher.integrations import mcp_server
from doc_searcher.config import AppConfig
from doc_searcher.search.search_service import SearchService, document_uri

SAMPLE_DIR = Path(__file__).parent / "sample_files"
SRC_DIR = Path(__file__).parent.parent / "src"


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
    assert [t.uri_template for t in templates.resource_templates] == [
        "docsearcher://document/{path}"
    ]


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
            await client.call_tool(
                "reindex_directory", {"directory": "/definitely/not/configured"}
            ),
        ]

    bad_format, bad_query, bad_dir = run_client(scenario)
    assert all(result.is_error for result in (bad_format, bad_query, bad_dir))
    assert "not inside a configured search folder" in bad_dir.content[0].text


def test_unindexed_document_resource_is_not_found(indexed_service):
    async def scenario(client):
        with pytest.raises(Exception, match="not in the index"):
            await client.read_resource(document_uri(mcp_server.__file__))

    run_client(scenario)


def test_stdio_server_round_trip_survives_stray_prints(tmp_path):
    """Launch the real server; a broken config.json makes AppConfig log a warning."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "config.json").write_text("{not json", encoding="utf-8")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "doc_searcher.integrations.mcp_server"],
        env={"DOC_SEARCHER_DATA_DIR": str(data_dir), "PYTHONPATH": str(SRC_DIR)},
        cwd=str(tmp_path),
    )

    errlog_path = tmp_path / "server_stderr.log"

    async def main():
        with open(errlog_path, "w", encoding="utf-8") as errlog:
            async with Client(
                stdio_client(params, errlog=errlog), read_timeout_seconds=60
            ) as client:
                return await client.call_tool("get_index_status", {})

    status = anyio.run(main)

    # The warning really happened (on the child's stderr) and the protocol survived it.
    assert "Failed to load config" in errlog_path.read_text(encoding="utf-8")
    assert not status.is_error
    assert status.structured_content["total_documents"] == 0
    assert status.structured_content["index_path"] == str(data_dir / "index.db")
    json.dumps(status.structured_content)  # JSON-ready payload


def test_unusable_index_becomes_a_tool_error(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "index.db").write_bytes(b"not a database" * 100)
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(data_dir))
    monkeypatch.setattr(mcp_server, "_service", None)

    async def scenario(client):
        return await client.call_tool("get_index_status", {})

    result = run_client(scenario)
    assert result.is_error
    assert "damaged" in result.content[0].text


def _broken_pdf(path):
    """A damaged PDF: MuPDF reports 'MuPDF error: ...' while extracting it."""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello world")
    raw = doc.tobytes()
    doc.close()
    # Opens fine, but MuPDF reports "object is not a stream" while extracting the page.
    path.write_bytes(raw.replace(b"stream", b"strean", 1))


def test_stdio_protocol_stays_valid_while_mupdf_reports_errors(tmp_path):
    data_dir = tmp_path / "data"
    docs = tmp_path / "docs"
    docs.mkdir()
    _broken_pdf(docs / "broken.pdf")
    (docs / "ok.txt").write_text("keyword", encoding="utf-8")
    data_dir.mkdir()
    (data_dir / "config.json").write_text(
        json.dumps({"directories": [str(docs)]}), encoding="utf-8"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "doc_searcher.integrations.mcp_server"],
        env={"DOC_SEARCHER_DATA_DIR": str(data_dir), "PYTHONPATH": str(SRC_DIR)},
        cwd=str(tmp_path),
    )
    errlog_path = tmp_path / "server_stderr.log"

    async def main():
        with open(errlog_path, "w", encoding="utf-8") as errlog:
            async with Client(
                stdio_client(params, errlog=errlog), read_timeout_seconds=60
            ) as client:
                await client.call_tool("reindex_directory", {})
                for _ in range(300):
                    status = await client.call_tool("get_index_status", {})
                    if status.structured_content["reindex"]["state"] != "running":
                        break
                    await anyio.sleep(0.1)
                search = await client.call_tool("search_documents", {"query": "keyword"})
                return status, search

    status, search = anyio.run(main)
    assert status.structured_content["reindex"]["state"] == "completed"
    assert not search.is_error and search.structured_content["result_count"] == 1
    assert "MuPDF error" in errlog_path.read_text(encoding="utf-8")  # logged on stderr, not stdout
