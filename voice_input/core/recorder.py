"""麦克风录音（sounddevice），输出 16k mono PCM16LE 异步流。"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional

try:
    import sounddevice as sd
except (ImportError, OSError) as exc:  # PortAudio 未安装或 sounddevice 未安装
    sd = None  # type: ignore[assignment]
    _IMPORT_ERROR: Optional[BaseException] = exc
else:
    _IMPORT_ERROR = None


class Recorder:
    """异步 PCM 录音器。

    使用方式::

        rec = Recorder()
        await rec.start()
        async for pcm in rec.chunks():
            ...
        await rec.stop()
    """

    _SENTINEL = b""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_ms: int = 100,
        input_device: Optional[int] = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_samples = sample_rate * frame_ms // 1000
        self._input_device = input_device
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stream = None
        self._stopped = False

    @property
    def selected_input_device(self) -> Optional[int]:
        return self._input_device

    def set_input_device(self, device_id: Optional[int]) -> None:
        self._input_device = device_id

    def list_input_devices(self) -> list[dict[str, Any]]:
        if sd is None:
            raise RuntimeError(
                f"sounddevice 不可用（PortAudio 未安装？）: {_IMPORT_ERROR}"
            )
        default_input = _default_input_device_id()
        devices = []
        for idx, device in enumerate(sd.query_devices()):
            channels = int(device.get("max_input_channels", 0))
            if channels <= 0:
                continue
            selected = (
                idx == self._input_device
                if self._input_device is not None
                else idx == default_input
            )
            devices.append(
                {
                    "id": idx,
                    "name": str(device.get("name", f"Device {idx}")),
                    "channels": channels,
                    "default": idx == default_input,
                    "selected": selected,
                }
            )
        return devices

    async def start(self) -> None:
        if sd is None:
            raise RuntimeError(
                f"sounddevice 不可用（PortAudio 未安装？）: {_IMPORT_ERROR}"
            )
        self._loop = asyncio.get_running_loop()
        self._stopped = False
        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            blocksize=self._frame_samples,
            device=self._input_device,
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        """sounddevice 回调（在音频线程）。"""
        if self._stopped:
            return
        self._enqueue(bytes(indata))

    def _enqueue(self, data: bytes) -> None:
        """线程安全地塞进 asyncio 队列（测试可重写为同步入队）。"""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, data)

    async def stop(self) -> None:
        self._stopped = True
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self._queue.put_nowait(self._SENTINEL)

    async def chunks(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._queue.get()
            if chunk == self._SENTINEL:
                return
            yield chunk


def _default_input_device_id() -> Optional[int]:
    try:
        default = sd.default.device  # type: ignore[union-attr]
    except Exception:
        return None
    if isinstance(default, (tuple, list)):
        try:
            return int(default[0])
        except (TypeError, ValueError, IndexError):
            return None
    try:
        return int(default)
    except (TypeError, ValueError):
        return None
