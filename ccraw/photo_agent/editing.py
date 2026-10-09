"""Photo Engine proposals, immutable disclosure and independent derived versions."""

import base64
import copy
import hashlib
import json
import threading

from .. import engine, model, nl_edit, image_generation as generation
from .models import digest
from .store import identifier


def check_original(project, photo_id, cancel=None):
    photo = project.photo(photo_id)
    if not photo['sha'] or digest(photo['path'], cancel) != photo['sha']:
        raise ValueError('原片已变化或尚未完成扫描，请重新扫描后创建方案。')
    return photo


def diagnose(project, photo_id):
    photo = check_original(project, photo_id)
    facts = photo['facts']
    return dict(
        photo_id=photo_id,
        dimensions=[facts['width'], facts['height']],
        flags=facts.get('flags', []),
        quality=facts.get('quality', {}),
        note='本地统计诊断；质量提示需要人工判断。',
    )


def propose_local(project, photo_id, patch=None):
    diagnostic = diagnose(project, photo_id)
    photo = project.photo(photo_id)
    choices = [
        ('自然增强', dict(adjustments={'contrast': 8, 'vibrance': 12, 'shadows': 10})),
        ('柔和光影', dict(adjustments={'highlights': -20, 'shadows': 20, 'contrast': -5})),
        ('暖色氛围', dict(adjustments={'temperature': 12, 'vibrance': 8})),
    ]
    if '曝光偏低' in diagnostic['flags']:
        choices[0][1]['adjustments']['exposure'] = 0.5
    if patch is not None:
        if not isinstance(patch, dict):
            raise ValueError('编辑参数必须是对象。')
        choices = [('自定义调整', patch)]
    base = project.preferences().get('recipe:' + photo_id, model.recipe())
    result = []
    for label, change in choices[:3]:
        plan = nl_edit.plan(model.validate(base), change)
        if plan.regions:
            raise ValueError('自动选区请在照片编辑模式完成，或提供完整已有蒙版配方。')
        payload = dict(
            label=label,
            recipe=model.validate(plan.target),
            diagnostic=diagnostic,
            source_sha256=photo['sha'],
            engine='CCRaw Photo Engine',
            upload='无',
            cost='本地处理',
        )
        proposal_id = project.proposal(photo_id, 'local', payload)
        result.append(
            dict(id=proposal_id, photo_id=photo_id, kind='local', payload=payload, status='pending')
        )
    project.event('edit.proposed', {'photo_id': photo_id, 'count': len(result), 'kind': 'local'})
    return result


def apply_local(project, proposal_id, cancel=None):
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise InterruptedError('处理已取消。')
    proposal = project.claim_proposal(proposal_id)
    output = None
    try:
        if proposal['kind'] != 'local':
            raise ValueError('请选择本地编辑方案。')
        photo = check_original(project, proposal['photo_id'], cancel)
        payload = proposal['payload']
        if photo['sha'] != payload['source_sha256']:
            raise ValueError('原片已变化，请重新创建方案。')
        source, info = engine.load_image(photo['path'], 0, develop_reference=True)
        recipe = model.validate(payload['recipe'])
        if (
            info.get('raw')
            and recipe['develop']['mode'] == 'camera'
            and not recipe['develop'].get('curve')
        ):
            recipe['develop'].update(info.get('develop', {}))
        if cancel.is_set():
            raise InterruptedError('处理已取消。')
        rendered = engine.process(source, recipe, engine.Backend('auto'))
        check_original(project, photo['id'], cancel)
        output = project.output(identifier() + '.tif')
        provenance = dict(
            source_sha256=photo['sha'],
            recipe=recipe,
            proposal_id=proposal_id,
            provider='CCRaw Photo Engine',
        )
        engine.export_image(output, rendered, photo=info.get('photo', {}), provenance=provenance)
        if cancel.is_set():
            raise InterruptedError('处理已取消。')
        project.derivative(photo['id'], output, provenance)
        project.proposal_status(proposal_id, 'completed')
        project.event(
            'edit.completed',
            {'photo_id': photo['id'], 'proposal_id': proposal_id, 'output': output.name},
        )
        return str(output)
    except BaseException:
        if output is not None:
            output.unlink(missing_ok=True)
        project.proposal_status(proposal_id, 'failed')
        raise


def settings_digest(settings):
    values = {
        key: settings.get(key) for key in ('base_url', 'model', 'key', 'timeout', 'watermark')
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def disclosure_digest(payload):
    value = {k: v for k, v in payload.items() if k != 'disclosure_digest'}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def propose_external(project, photo_id, prompt, settings, *, unit_cost=None):
    photo = check_original(project, photo_id)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise ValueError('提示词需要 1–12000 字。')
    nl_edit.validate_endpoint(settings['base_url'])
    if not settings.get('model'):
        raise ValueError('请先配置生成模型。')
    recipe = model.validate(project.preferences().get('recipe:' + photo_id, model.recipe()))
    reference = generation.prepare_reference(photo['path'], recipe)
    check_original(project, photo_id)
    image = base64.b64decode(reference.split(',', 1)[1])
    from PIL import Image
    import io

    with Image.open(io.BytesIO(image)) as preview:
        size = list(preview.size)
    reference_id = identifier() + '.reference.jpg'
    with (project.directory('previews') / reference_id).open('xb') as stream:
        stream.write(image)
    if unit_cost is not None and (
        type(unit_cost) not in (int, float) or not 0 <= unit_cost < 1_000_000
    ):
        raise ValueError('单张费用无效。')
    payload = dict(
        label='外部图像编辑',
        provider=settings['base_url'],
        model=settings['model'],
        prompt=prompt.strip(),
        upload=dict(
            content='处理后的 JPEG 参考图，不含 EXIF；同时发送提示词',
            dimensions=size,
            bytes=len(image),
            sha256=hashlib.sha256(image).hexdigest(),
        ),
        cost=f'预计 {unit_cost:.2f} / 张（服务商实际结算）'
        if unit_cost is not None
        else '费用未知，按服务商计费；提交后取消可能仍收费',
        reference=reference_id,
        source_sha256=photo['sha'],
        settings_digest=settings_digest(settings),
        size='2K',
    )
    payload['disclosure_digest'] = disclosure_digest(payload)
    proposal_id = project.proposal(photo_id, 'external', payload)
    project.event(
        'external.proposed',
        {
            'proposal_id': proposal_id,
            'provider': payload['provider'],
            'upload': payload['upload'],
            'cost': payload['cost'],
        },
    )
    return dict(
        id=proposal_id, photo_id=photo_id, kind='external', status='pending', payload=payload
    )


def execute_external(project, proposal_id, settings, *, approved_digest, client=None, cancel=None):
    proposal = next((p for p in project.proposals() if p['id'] == proposal_id), None)
    if proposal is None or proposal['kind'] != 'external':
        raise ValueError('外部编辑方案无效。')
    payload = proposal['payload']
    if (
        approved_digest != payload['disclosure_digest']
        or disclosure_digest(payload) != approved_digest
        or settings_digest(settings) != payload['settings_digest']
    ):
        raise ValueError('Provider、上传内容或配置发生变化，请重新创建并确认方案。')
    photo = check_original(project, proposal['photo_id'], cancel)
    if photo['sha'] != payload['source_sha256']:
        raise ValueError('原片已变化，请重新创建并确认方案。')
    reference_path = project.directory('previews') / payload['reference']
    if (
        reference_path.resolve().parent != (project.root / 'previews').resolve()
        or not reference_path.is_file()
    ):
        raise ValueError('上传图片发生变化，请重新创建方案。')
    reference_bytes = reference_path.read_bytes()
    if hashlib.sha256(reference_bytes).hexdigest() != payload['upload']['sha256']:
        raise ValueError('上传图片发生变化，请重新创建方案。')
    if cancel is not None and cancel.is_set():
        raise InterruptedError('外部编辑已取消。')
    project.claim_proposal(proposal_id)
    output = None
    try:
        client = client or generation.GenerationClient()
        reference = 'data:image/jpeg;base64,' + base64.b64encode(reference_bytes).decode()
        data = client.generate(
            copy.deepcopy(settings), payload['prompt'], reference, payload['size']
        )
        extension = generation.image_format(data)
        output = project.output(identifier() + extension)
        if cancel is not None and cancel.is_set():
            raise InterruptedError('等待已取消，服务商可能仍处理已提交请求。')
        output.write_bytes(data)
        provenance = dict(
            source_sha256=payload['source_sha256'],
            proposal_id=proposal_id,
            provider=payload['provider'],
            model=payload['model'],
            prompt=payload['prompt'],
            disclosure_digest=approved_digest,
        )
        project.derivative(proposal['photo_id'], output, provenance)
        project.proposal_status(proposal_id, 'completed')
        project.event('external.completed', {'proposal_id': proposal_id, 'output': output.name})
        return str(output)
    except BaseException:
        if output is not None:
            output.unlink(missing_ok=True)
        project.proposal_status(proposal_id, 'interrupted')
        raise
