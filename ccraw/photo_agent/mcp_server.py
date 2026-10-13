"""Stdio MCP adapter for the same constrained photo tools used by the Agent UI."""

import argparse
import asyncio
import base64
import json
import threading

from .models import LocalModels
from .plugins import photo_registry, bounded_result
from .store import Project
from ..logs import redact


async def invoke_tool(project, selected, name, arguments, models=None):
    from mcp.types import CallToolResult, TextContent, ImageContent
    from jsonschema import validate, ValidationError

    cancel = threading.Event()
    registry = photo_registry(project, selected, cancel, models or LocalModels())
    try:
        if name not in registry.tools:
            raise ValueError('工具不存在。')
        spec = registry.tools[name]
        validate(arguments, spec.parameters)
        project.event('tool.started', {'name': name, 'transport': 'mcp'})
        result = await asyncio.to_thread(spec.handler, **arguments)
        bounded = bounded_result(result)
        content = [
            TextContent(type='text', text=json.dumps(bounded, ensure_ascii=False, allow_nan=False))
        ]
        if name == 'preview_edit':
            folder = project.directory('previews')
            for key in ('before', 'after'):
                path = folder / result[key]
                if (
                    path.is_symlink()
                    or path.resolve().parent != folder
                    or path.stat().st_size > 8 * 1024 * 1024
                ):
                    raise ValueError('预览图像路径或大小无效。')
                content.append(
                    ImageContent(
                        type='image',
                        mimeType='image/jpeg',
                        data=base64.b64encode(path.read_bytes()).decode('ascii'),
                    )
                )
        project.event('tool.finished', {'name': name, 'transport': 'mcp', 'result': bounded})
        return CallToolResult(content=content, structuredContent=bounded)
    except asyncio.CancelledError:
        cancel.set()
        raise
    except (ValueError, OSError, ValidationError, TypeError) as error:
        message = (
            '工具参数不符合 schema。'
            if isinstance(error, ValidationError)
            else redact(str(error))[:400]
        )
        project.event('tool.failed', {'name': name, 'transport': 'mcp', 'error': message})
        return CallToolResult(content=[TextContent(type='text', text=message)], isError=True)


def create_server(project, selected):
    from mcp.server.lowlevel import Server
    from mcp.types import Tool, ToolAnnotations

    selected = tuple(selected)
    if len(selected) > 30 or len(set(selected)) != len(selected):
        raise ValueError('MCP 一次最多选择 30 张不同的照片。')
    for photo_id in selected:
        project.photo(photo_id)
    models = LocalModels()
    registry = photo_registry(project, selected, threading.Event(), models)
    server = Server('CCRaw Photo Engine')
    work = asyncio.Semaphore(1)

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.parameters,
                annotations=ToolAnnotations(
                    readOnlyHint=spec.read_only, destructiveHint=False, openWorldHint=False
                ),
            )
            for spec in registry.tools.values()
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        async with work:
            return await invoke_tool(project, selected, name, arguments, models)

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description='CCRaw Photo Engine MCP (stdio)')
    parser.add_argument('project', help='.ccrawagent 工程路径')
    parser.add_argument(
        '--select', action='append', default=[], help='允许创建编辑方案的工程照片 ID，可重复'
    )
    args = parser.parse_args(argv)
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        parser.exit(2, '请先安装 requirements-agent.txt 以启用 MCP。\n')
    project = Project.open(args.project)
    server = create_server(project, args.select)

    async def serve():
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())

    asyncio.run(serve())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
