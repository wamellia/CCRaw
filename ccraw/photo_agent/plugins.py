"""Capability descriptors and photo-only tool adapters for the nanobot registry."""

from dataclasses import dataclass
import asyncio
import json

from . import analysis, editing, search
from .. import image_generation


def bounded_result(value):
    """Keep complete JSON at the model boundary, with explicit omitted counts."""

    def trim(item, cap):
        if isinstance(item, dict):
            result = {key: trim(value, cap) for key, value in item.items()}
            for key, value in item.items():
                if isinstance(value, list) and len(value) > cap:
                    result[key + '_omitted'] = len(value) - cap
            return result
        if isinstance(item, list):
            return [trim(value, cap) for value in item[:cap]]
        if isinstance(item, str):
            return item[:1000]
        return item

    for cap in (20, 10, 5, 2, 1):
        result = trim(value, cap)
        if len(json.dumps(result, ensure_ascii=False)) <= 5500:
            return result
    raise ValueError('工具结果过大，请缩小任务范围。')


TOOL_NAMES = (
    'album_report',
    'search_photos',
    'diagnose_photo',
    'recommend_edits',
    'propose_adjustment',
    'propose_external_edit',
)


@dataclass(frozen=True)
class Capability:
    name: str
    tools: tuple[str, ...]
    permissions: tuple[str, ...]


def capabilities():
    return {
        'organize': Capability('organize', ('album_report',), ('project.read',)),
        'retrieve': Capability('retrieve', ('search_photos',), ('project.read', 'local.models')),
        'edit': Capability('edit', TOOL_NAMES[2:], ('selected.read', 'proposal.write')),
    }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    capability: str
    description: str
    parameters: dict
    handler: object
    read_only: bool = True


class PluginRegistry:
    def __init__(self):
        self.capabilities = {}
        self.tools = {}

    def register(self, capability, tools):
        if capability.name in self.capabilities:
            raise ValueError('能力名称已注册。')
        tools = list(tools)
        if {t.name for t in tools} != set(capability.tools) or any(
            t.name in self.tools or t.capability != capability.name for t in tools
        ):
            raise ValueError('工具与能力声明不一致。')
        self.capabilities[capability.name] = capability
        self.tools.update({tool.name: tool for tool in tools})


def photo_registry(project, selected, cancel, models):
    registry = PluginRegistry()

    def selected_photo(photo_id):
        if photo_id not in selected:
            raise ValueError('请先在界面选中需要修改的照片。')
        project.photo(photo_id)
        return photo_id

    def report():
        result = analysis.suggestions(project)
        return dict(
            summary=project.summary(),
            suggestions={k: v[:30] for k, v in result.items() if isinstance(v, list)},
            coverage=result['coverage'],
            features=result['features'],
        )

    def retrieve(query):
        return search.search(project, query, models=models)

    def diagnosis(photo_id):
        return editing.diagnose(project, selected_photo(photo_id))

    def recommend(photo_id):
        return proposals(editing.propose_local(project, selected_photo(photo_id)))

    def adjust(photo_id, patch):
        return proposals(editing.propose_local(project, selected_photo(photo_id), patch))

    def external(photo_id, prompt):
        proposal = editing.propose_external(
            project,
            selected_photo(photo_id),
            prompt,
            image_generation.load_settings(),
            unit_cost=project.preferences().get('external_unit_cost'),
        )
        return dict(
            proposal_id=proposal['id'],
            provider=proposal['payload']['provider'],
            upload=proposal['payload']['upload'],
            cost=proposal['payload']['cost'],
            status='awaiting UI approval; not submitted',
        )

    def proposals(items):
        return dict(
            proposals=[
                dict(
                    id=p['id'],
                    label=p['payload']['label'],
                    adjustments=p['payload']['recipe']['adjustments'],
                    status='awaiting UI selection',
                )
                for p in items
            ]
        )

    photo_parameter = dict(type='string', description='当前界面选中的工程照片 ID')

    def params(properties, required=()):
        return dict(
            type='object',
            properties=properties,
            required=list(required),
            additionalProperties=False,
        )

    query_properties = {
        'terms': {'type': 'string', 'maxLength': 1000},
        'semantic_text': {'type': 'string', 'maxLength': 1000},
        'from_date': {'type': 'string'},
        'to_date': {'type': 'string'},
        'person_count': {'type': 'integer', 'minimum': 0, 'maximum': 50},
        'strict_people': {'type': 'boolean'},
        'person_cluster': {'type': 'string'},
        'quality': {'type': 'string', 'enum': ['any', 'good', 'low']},
        'tags': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 12},
        'photo_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 500},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
    }
    definitions = [
        ToolSpec(
            'album_report',
            'organize',
            '读取本工程重复、相似、低质、事件和匿名人物簇建议；建议不会删除照片。',
            params({}),
            report,
        ),
        ToolSpec(
            'search_photos',
            'retrieve',
            '使用受约束查询找图；英文 semantic_text 用于本地 CLIP，日期根据拍摄时间，人数未知必须标明。',
            params({'query': params(query_properties)}, ['query']),
            retrieve,
        ),
        ToolSpec(
            'diagnose_photo',
            'edit',
            '对选中照片执行本地质量诊断。',
            params({'photo_id': photo_parameter}, ['photo_id']),
            diagnosis,
        ),
        ToolSpec(
            'recommend_edits',
            'edit',
            '诊断后创建最多三种本地编辑方案，等待界面选择应用。',
            params({'photo_id': photo_parameter}, ['photo_id']),
            recommend,
            False,
        ),
        ToolSpec(
            'propose_adjustment',
            'edit',
            '根据自然语言创建无损编辑方案。patch 使用 CCRaw adjustments、hsl、grading、curve、effects 字段；不会执行编辑。',
            params(
                {'photo_id': photo_parameter, 'patch': {'type': 'object'}}, ['photo_id', 'patch']
            ),
            adjust,
            False,
        ),
        ToolSpec(
            'propose_external_edit',
            'edit',
            '为选中照片准备外部图像编辑方案并展示 Provider、上传内容及费用；必须等待界面确认，不会上传。',
            params(
                {'photo_id': photo_parameter, 'prompt': {'type': 'string', 'maxLength': 12000}},
                ['photo_id', 'prompt'],
            ),
            external,
            False,
        ),
    ]
    for capability in capabilities().values():
        registry.register(capability, [t for t in definitions if t.capability == capability.name])
    return registry


def nanobot_registry(plugins, project, cancel, emit):
    from nanobot.agent.tools.base import Tool
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.agent.tools.context import current_request_context

    class PhotoTool(Tool):
        def __init__(self, spec):
            self.spec = spec

        @property
        def name(self):
            return self.spec.name

        @property
        def description(self):
            return self.spec.description

        @property
        def parameters(self):
            return self.spec.parameters

        @property
        def read_only(self):
            return self.spec.read_only

        async def execute(self, **kwargs):
            request = current_request_context()
            if request is None or request.attributes.get('project_id') != project.manifest['id']:
                return self.error('工程上下文不匹配。')
            if cancel.is_set():
                raise asyncio.CancelledError()
            try:
                result = await asyncio.to_thread(self.spec.handler, **kwargs)
                bounded = bounded_result(result)
                project.event('tool.finished', {'name': self.name, 'result': bounded})
                emit('tool.finished', dict(name=self.name, result=result))
                return json.dumps(bounded, ensure_ascii=False, allow_nan=False)
            except (ValueError, OSError) as error:
                project.event('tool.failed', {'name': self.name, 'error': str(error)[:400]})
                return self.error(str(error)[:400])

    result = ToolRegistry()
    for tool in plugins.tools.values():
        result.register(PhotoTool(tool))
    return result
