"""Optional local CLIP and anonymous face embeddings; verified explicit downloads."""

import hashlib
import json
import os
from pathlib import Path
import threading
import urllib.request
import uuid

import cv2
import numpy as np
from PIL import Image

from .. import host, performance

MANIFEST = json.loads(
    (Path(__file__).parents[1] / 'resources' / 'agent-models.json').read_text(encoding='utf8')
)
SIGNATURE = MANIFEST['signature']
SCENES = (
    ('海边', 'a photo of a beach by the sea'),
    ('日落', 'a photo of a sunset'),
    ('山地', 'a photo of mountains'),
    ('城市', 'a photo of a city'),
    ('人像', 'a portrait photo'),
    ('食物', 'a photo of food'),
    ('动物', 'a photo of an animal'),
    ('室内', 'a photo indoors'),
    ('雪景', 'a photo of snow'),
    ('夜景', 'a photo taken at night'),
)


def model_root():
    return Path(
        os.environ.get('CCRAW_AGENT_MODEL_DIR', host.data_folder() / 'agent-models')
    ).resolve()


def digest(path, cancel=None):
    sha = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise InterruptedError('操作已取消。')
            sha.update(data)
    return sha.hexdigest()


def install_models(cancel=None, progress=lambda *_: None):
    root = model_root()
    root.mkdir(parents=True, exist_ok=True)
    for i, spec in enumerate(MANIFEST['files']):
        path = root / spec['name']
        if (
            path.is_file()
            and path.stat().st_size == spec['bytes']
            and digest(path, cancel) == spec['sha256']
        ):
            continue
        temporary = path.with_suffix(path.suffix + '.' + uuid.uuid4().hex + '.download')
        try:
            request = urllib.request.Request(
                spec['url'], headers={'User-Agent': 'CCRaw-model-setup'}
            )
            with (
                urllib.request.urlopen(request, timeout=45) as response,
                temporary.open('wb') as output,
            ):
                total = 0
                while chunk := response.read(1024 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError('模型下载已取消。')
                    total += len(chunk)
                    if total > spec['bytes']:
                        raise ValueError('模型下载超过预期大小。')
                    output.write(chunk)
                    progress(
                        int((i + total / spec['bytes']) / len(MANIFEST['files']) * 100),
                        spec['name'],
                    )
            if total != spec['bytes'] or digest(temporary, cancel) != spec['sha256']:
                raise ValueError('模型校验失败。')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        'installed': len(MANIFEST['files']),
        'bytes': sum(s['bytes'] for s in MANIFEST['files']),
    }


def normalized(vector):
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not 1 <= len(value) <= 4096 or not np.isfinite(value).all():
        raise ValueError('向量无效。')
    norm = np.linalg.norm(value)
    if norm < 1e-8:
        raise ValueError('空向量无效。')
    return value / norm


class LocalModels:
    def __init__(self):
        self.root = model_root()
        self.lock = threading.RLock()
        self.sessions = {}
        self.detector = self.recognizer = self.tokenizer = None
        self.scene_vectors = None
        self.clip_ready = all(
            self._present(n)
            for n in ('vision_model_quantized.onnx', 'text_model_quantized.onnx', 'tokenizer.json')
        )
        self.face_ready = all(
            self._present(n)
            for n in ('face_detection_yunet_2023mar.onnx', 'face_recognition_sface_2021dec.onnx')
        )
        self.signature = SIGNATURE + f':clip={self.clip_ready}:face={self.face_ready}'

    def _present(self, name):
        spec = next(s for s in MANIFEST['files'] if s['name'] == name)
        path = self.root / name
        return (
            path.is_file()
            and path.stat().st_size == spec['bytes']
            and digest(path) == spec['sha256']
        )

    def _session(self, kind):
        if kind not in self.sessions:
            import onnxruntime as ort

            self.sessions[kind] = ort.InferenceSession(
                str(self.root / f'{kind}_model_quantized.onnx'),
                sess_options=performance.session_options(),
                providers=['CPUExecutionProvider'],
            )
        return self.sessions[kind]

    def text_vector(self, text):
        if not self.clip_ready:
            return None
        with self.lock:
            from tokenizers import Tokenizer

            if self.tokenizer is None:
                self.tokenizer = Tokenizer.from_file(str(self.root / 'tokenizer.json'))
                self.tokenizer.enable_truncation(max_length=77)
                self.tokenizer.enable_padding(length=77, pad_id=49407, pad_token='<|endoftext|>')
            tokens = self.tokenizer.encode(text[:1000])
            session = self._session('text')
            values = dict(
                input_ids=np.array([tokens.ids], np.int64),
                attention_mask=np.array([tokens.attention_mask], np.int64),
            )
            inputs = {v.name: values[v.name] for v in session.get_inputs()}
            return normalized(session.run(['text_embeds'], inputs)[0][0])

    def analyze(self, image):
        result = dict(
            vector=None,
            scenes=[],
            faces=[],
            features=dict(semantic=self.clip_ready, faces=self.face_ready),
        )
        with self.lock:
            if self.clip_ready:
                width, height = image.size
                scale = 224 / min(width, height)
                resized = image.resize(
                    (round(width * scale), round(height * scale)), Image.Resampling.BICUBIC
                )
                x, y = (resized.width - 224) // 2, (resized.height - 224) // 2
                pixels = np.asarray(resized.crop((x, y, x + 224, y + 224)), np.float32) / 255
                pixels = (pixels - [0.48145466, 0.4578275, 0.40821073]) / [
                    0.26862954,
                    0.26130258,
                    0.27577711,
                ]
                session = self._session('vision')
                vector = normalized(
                    session.run(
                        ['image_embeds'],
                        {'pixel_values': pixels.transpose(2, 0, 1)[None].astype(np.float32)},
                    )[0][0]
                )
                result['vector'] = vector.tolist()
                if self.scene_vectors is None:
                    self.scene_vectors = np.stack([self.text_vector(text) for _, text in SCENES])
                scores = self.scene_vectors @ vector
                result['scenes'] = [
                    dict(tag=SCENES[i][0], similarity=float(scores[i]))
                    for i in scores.argsort()[-3:][::-1]
                    if scores[i] > 0.2
                ]
            if self.face_ready:
                if self.detector is None:
                    self.detector = cv2.FaceDetectorYN.create(
                        str(self.root / 'face_detection_yunet_2023mar.onnx'),
                        '',
                        (640, 640),
                        0.9,
                        0.3,
                        200,
                    )
                    self.recognizer = cv2.FaceRecognizerSF.create(
                        str(self.root / 'face_recognition_sface_2021dec.onnx'), ''
                    )
                # The pinned YuNet graph is fixed 640x640, including OpenCV 5 ORT.
                bgr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
                h, w = bgr.shape[:2]
                scale = min(640 / h, 640 / w)
                resized = cv2.resize(bgr, (round(w * scale), round(h * scale)))
                canvas = np.zeros((640, 640, 3), np.uint8)
                canvas[: resized.shape[0], : resized.shape[1]] = resized
                _, faces = self.detector.detect(canvas)
                for face in () if faces is None else faces[:50]:
                    aligned = self.recognizer.alignCrop(canvas, face)
                    vector = normalized(self.recognizer.feature(aligned))
                    result['faces'].append(
                        dict(
                            box=(
                                face[:4]
                                / [
                                    resized.shape[1],
                                    resized.shape[0],
                                    resized.shape[1],
                                    resized.shape[0],
                                ]
                            ).tolist(),
                            vector=vector.tolist(),
                            confidence=float(face[-1]),
                        )
                    )
        return result
