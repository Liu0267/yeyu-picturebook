"""语音链路：本地 ASR（mlx-whisper-small，子进程隔离调用）+ TTS 调度。

ASR 模型 work/models/whisper-small-mlx（MIT，本地推理，无上传）。
original Qwen3-ASR-1.7B 模型文件已被清理；本模块保留 ASR_BACKEND 抽象，
模型恢复后可切回（qwen-asr CLI 或 serve 模式），前端无需改动。

TTS P0：由前端浏览器 speechSynthesis 播报（speech.py 不实现）；
P1 可接入常驻 tts_worker（Qwen3-TTS），见 config.TTS_* 预留。
"""

import json
import subprocess
import tempfile
import time
from pathlib import Path

from . import config

ROOT = config.REPO_ROOT
QA_PY = ROOT.parent.parent / 'work' / 'audio-qa-env' / 'bin/python'
WHISPER_DIR = ROOT.parent.parent / 'work' / 'models' / 'whisper-small-mlx'
INITIAL_PROMPT = '以下是普通话的简体中文句子。'


class SpeechError(Exception):
    def __init__(self, code, message, http=502):
        self.code, self.message, self.http = code, message, http
        super().__init__(message)


def _transcript_script(audio_path: str) -> str:
    return (
        "import json, mlx_whisper\n"
        f"r = mlx_whisper.transcribe({audio_path!r}, path_or_hf_repo={str(WHISPER_DIR)!r},"
        f" language='zh', initial_prompt={INITIAL_PROMPT!r})\n"
        "print(json.dumps({'text': r['text'].strip()}, ensure_ascii=False))\n"
    )


def transcribe(data: bytes, suffix='.webm') -> dict:
    """音频字节 → {'text': str, 'elapsed_ms': int}。失败抛 SpeechError。"""
    if not data:
        raise SpeechError('EMPTY_AUDIO', '没有收到录音。', 400)
    if len(data) > 3 * 1024 * 1024:
        raise SpeechError('AUDIO_TOO_LARGE', '录音超过8秒限制。', 413)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as td:
        audio = Path(td) / f'input{suffix}'
        audio.write_bytes(data)
        if not QA_PY.is_file():
            raise SpeechError('ASR_UNAVAILABLE', '本机语音识别环境未安装。')
        try:
            proc = subprocess.run(
                [str(QA_PY), '-c', _transcript_script(str(audio))],
                capture_output=True, timeout=30,
                env={'HF_HUB_OFFLINE': '1', 'HF_HOME': str(ROOT.parent.parent / 'work' / 'qa-hf-cache'),
                     'PATH': '/usr/bin:/bin:/usr/local/bin', 'TMPDIR': td},
            )
        except subprocess.TimeoutExpired:
            raise SpeechError('ASR_TIMEOUT', '识别超时，请说短一点再试。')
        if proc.returncode != 0:
            raise SpeechError('ASR_FAILED', '语音识别失败，请重试或用文字输入。')
        try:
            payload = json.loads(proc.stdout.decode().strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise SpeechError('ASR_BAD_OUTPUT', '语音识别输出异常。')
    text = (payload.get('text') or '').strip()
    return dict(text=text, elapsed_ms=int((time.perf_counter() - started) * 1000))
