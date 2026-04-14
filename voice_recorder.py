"""
Mac端录音模块：麦克风录音 + 简单VAD + faster-whisper 本地转录

依赖：
    pip install faster-whisper sounddevice soundfile numpy
"""

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

_recording = False
_audio_frames = []
_record_thread: Optional[threading.Thread] = None
_record_start_time: Optional[float] = None

# Whisper 模型实例（懒加载，避免启动慢）
_whisper_model = None
_whisper_lock = threading.Lock()

# VAD 参数
VAD_SILENCE_THRESHOLD = 0.01    # RMS 低于此值视为静音
VAD_SILENCE_SECONDS = 30        # 连续静音超过30秒自动停止
SAMPLE_RATE = 16000             # 16kHz，whisper 标准采样率
CHANNELS = 1


def _get_whisper_model(model_size: str = "small"):
    """懒加载 whisper 模型（优先 faster-whisper，回退到 openai-whisper）"""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            try:
                from faster_whisper import WhisperModel
                _whisper_model = ("faster", WhisperModel(model_size, device="cpu", compute_type="int8"))
            except ImportError:
                try:
                    import whisper
                    _whisper_model = ("openai", whisper.load_model(model_size))
                except ImportError:
                    raise ImportError("请先安装 whisper：pip install openai-whisper 或 faster-whisper")
    return _whisper_model


def _rms(data) -> float:
    """计算音频帧的 RMS 音量"""
    import numpy as np
    return float(np.sqrt(np.mean(data.astype(float) ** 2))) / 32768.0


def start_recording(on_vad_stop: Optional[Callable[[], None]] = None) -> bool:
    """
    开始麦克风录音。
    on_vad_stop: VAD检测到长时间静音时的回调（可触发自动停止）
    返回 True=成功启动, False=已在录音中
    """
    global _recording, _audio_frames, _record_thread, _record_start_time

    if _recording:
        return False

    try:
        import sounddevice as sd
        import numpy as np
    except ImportError:
        raise ImportError("请先安装：pip install sounddevice numpy soundfile")

    _recording = True
    _audio_frames = []
    _record_start_time = time.time()

    silence_counter = [0]

    def audio_callback(indata, frames, time_info, status):
        if not _recording:
            return
        _audio_frames.append(indata.copy())

        vol = _rms(indata)
        if vol < VAD_SILENCE_THRESHOLD:
            silence_counter[0] += 1
        else:
            silence_counter[0] = 0

        silence_frames_limit = int(VAD_SILENCE_SECONDS * SAMPLE_RATE / frames)
        if silence_counter[0] >= silence_frames_limit and on_vad_stop:
            threading.Thread(target=on_vad_stop, daemon=True).start()
            silence_counter[0] = 0  # 防止重复触发

    def record_loop():
        import sounddevice as sd
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=1024,
            callback=audio_callback,
        ):
            while _recording:
                time.sleep(0.1)

    _record_thread = threading.Thread(target=record_loop, daemon=True)
    _record_thread.start()
    return True


def stop_recording() -> Optional[str]:
    """
    停止录音，将帧写入临时 WAV 文件。
    返回音频文件路径，若未在录音则返回 None。
    """
    global _recording, _record_start_time

    if not _recording:
        return None

    _recording = False
    if _record_thread:
        _record_thread.join(timeout=2)

    if not _audio_frames:
        return None

    import numpy as np
    import soundfile as sf

    audio_data = np.concatenate(_audio_frames, axis=0)
    tmp = tempfile.mktemp(suffix=".wav")
    sf.write(tmp, audio_data, SAMPLE_RATE, subtype="PCM_16")
    _record_start_time = None
    return tmp


def recording_duration() -> int:
    """返回当前录音时长（秒）"""
    if _record_start_time is None:
        return 0
    return int(time.time() - _record_start_time)


def is_recording() -> bool:
    return _recording


def transcribe_with_timestamps(audio_path: str, model_size: str = "small") -> list:
    """
    用 whisper 转录音频文件，返回带时间戳的片段列表。
    每个元素：{"start": float, "end": float, "text": str}
    """
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    backend, model = _get_whisper_model(model_size)

    if backend == "faster":
        try:
            segments, _ = model.transcribe(
                audio_path,
                language="zh",
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                initial_prompt="这是一段商务对话录音，内容可能涉及销售、客户拜访、项目沟通、产品介绍、需求讨论等场景。",
                condition_on_previous_text=False,
            )
            return [
                {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
                for seg in segments if seg.text.strip()
            ]
        except Exception as e:
            # 文件损坏或格式不支持，回退到空结果
            print(f"[转录] faster-whisper 解码失败（{Path(audio_path).name}）: {e}")
            return []
    else:
        result = model.transcribe(audio_path, language="zh", fp16=False)
        segs = result.get("segments", [])
        if segs:
            return [
                {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                for s in segs if s.get("text", "").strip()
            ]
        text = result.get("text", "").strip()
        return [{"start": 0.0, "end": 0.0, "text": text}] if text else []


def transcribe(audio_path: str, model_size: str = "small") -> str:
    """
    用 whisper 转录音频文件。
    优先使用 faster-whisper，若未安装则回退到 openai-whisper。
    首次调用会自动下载模型。返回转录文本。
    """
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    backend, model = _get_whisper_model(model_size)

    if backend == "faster":
        segments, _ = model.transcribe(
            audio_path,
            language="zh",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt="这是一段商务对话录音，内容可能涉及销售、客户拜访、项目沟通、产品介绍、需求讨论等场景。",
            condition_on_previous_text=False,
        )
        return "\n".join(seg.text.strip() for seg in segments if seg.text.strip())
    else:
        # openai-whisper
        result = model.transcribe(audio_path, language="zh", fp16=False)
        return result.get("text", "").strip()
