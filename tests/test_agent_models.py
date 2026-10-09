import os
from pathlib import Path

import numpy as np
import pytest

from ccraw.photo_agent.models import LocalModels
from ccraw.photo_agent.analysis import index_project, suggestions
from ccraw.photo_agent.store import Project
from ccraw.photo_agent.search import search


@pytest.mark.runtime_assets
def test_real_clip_and_anonymous_face_models(tmp_path):
    if not os.environ.get('CCRAW_AGENT_MODEL_DIR'):
        pytest.skip('Set CCRAW_AGENT_MODEL_DIR to verified optional Agent models')
    models = LocalModels()
    assert models.clip_ready and models.face_ready
    root = Path(__file__).resolve().parents[1] / 'ccraw/resources'
    portrait = root / 'generation-style-portrait.webp'
    landscape = root / 'generation-style-landscape.webp'
    project = Project.create(tmp_path / 'real.ccrawagent', '真实模型')
    ids = project.import_paths([portrait, landscape])
    original = portrait.read_bytes()
    result = index_project(project, models=models)
    assert result['analyzed'] == 2 and result['failed'] == 0
    facts = project.photo(ids[0])['facts']
    assert len(facts['vector']) == 512
    assert np.isclose(np.linalg.norm(facts['vector']), 1, atol=1e-4)
    assert len(facts['faces']) >= 1
    assert facts['person_count'] is None
    assert suggestions(project)['people']
    found = search(
        project, {'semantic_text': 'a photo of mountains and a landscape'}, models=models
    )
    assert found['semantic'] and found['photos'][0]['id'] == ids[1]
    assert portrait.read_bytes() == original
