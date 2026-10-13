import asyncio
import base64
import io
import json
import os
from pathlib import Path
import sys

from PIL import Image
import pytest

pytest.importorskip('mcp', reason='requires optional Agent MCP runtime')
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ccraw.photo_agent.plugins import TOOL_NAMES
from test_agent_controls import photo_project
from ccraw.photo_agent.mcp_server import invoke_tool


def test_real_stdio_mcp_discovery_proposal_and_image_preview(tmp_path):
    project, ids = photo_project(tmp_path, 2)
    before = [Path(project.photo(i)['path']).read_bytes() for i in ids]

    async def run():
        direct = await invoke_tool(project, ids[:1], 'get_edit_controls', {})
        assert not direct.isError
        args = [
            str(Path(__file__).parents[1] / 'main.py'),
            '--agent-mcp',
            str(project.path),
            '--select',
            ids[0],
        ]
        params = StdioServerParameters(
            command=sys.executable,
            args=args,
            env=dict(os.environ, PYTHONUTF8='1', CCRAW_DATA_DIR=str(tmp_path / 'settings')),
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == set(TOOL_NAMES)
                controls = await session.call_tool('get_edit_controls', {'photo_id': ids[0]})
                assert not controls.isError
                foreign = await session.call_tool(
                    'propose_adjustment', {'photo_id': ids[1], 'patch': {'rotation': 1}}
                )
                assert foreign.isError
                bad = await session.call_tool(
                    'propose_adjustment',
                    {'photo_id': ids[0], 'patch': {'adjustments': {'exposure': 999}}},
                )
                assert bad.isError
                result = await session.call_tool(
                    'propose_adjustment', {'photo_id': ids[0], 'patch': {'crop': [0.5, 0, 1, 1]}}
                )
                assert not result.isError
                proposal_id = json.loads(result.content[0].text)['proposals'][0]['id']
                rendered = await session.call_tool(
                    'preview_edit', {'proposal_id': proposal_id, 'max_edge': 512}
                )
                assert not rendered.isError
                images = [block for block in rendered.content if block.type == 'image']
                assert len(images) == 2
                for block, size in zip(images, [(200, 100), (100, 100)]):
                    assert block.mimeType == 'image/jpeg'
                    with Image.open(io.BytesIO(base64.b64decode(block.data))) as image:
                        assert image.size == size
                assert project.get_proposal(proposal_id)['status'] == 'pending'

    asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert project.derivatives() == []
    assert before == [Path(project.photo(i)['path']).read_bytes() for i in ids]
