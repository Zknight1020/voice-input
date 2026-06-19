"""协议层单测：握手帧 / 音频帧 / 服务器帧解析。"""
from __future__ import annotations

import gzip
import json
import struct

import pytest

from voice_input.core.doubao_asr import (
    TranscriptEvent,
    Utterance,
    _handshake_payload,
    build_audio_only_request,
    build_full_client_request,
    parse_server_frame,
)


def _server_frame(payload: dict, *, with_seq: bool = False) -> bytes:
    """伪造一帧豆包响应：JSON + gzip。"""
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    flags = 0x01 if with_seq else 0x00
    header = bytes([0x11, 0x90 | flags, 0x11, 0x00])
    seq = struct.pack(">I", 1) if with_seq else b""
    return header + seq + struct.pack(">I", len(body)) + body


class TestBuildFullClientRequest:
    def test_round_trip_payload(self) -> None:
        frame = build_full_client_request({"hello": "世界"})
        assert frame[:4] == bytes([0x11, 0x10, 0x11, 0x00])
        size = struct.unpack(">I", frame[4:8])[0]
        body = gzip.decompress(frame[8 : 8 + size])
        assert json.loads(body) == {"hello": "世界"}


class TestHandshakePayload:
    def test_default_request_unchanged(self) -> None:
        p = _handshake_payload()
        assert p["request"]["model_name"] == "bigmodel"
        assert p["request"]["enable_itn"] is True
        assert "context" not in p["request"]

    def test_extra_request_merges_into_request_subobject(self) -> None:
        p = _handshake_payload({"context": "前文：用户在讨论激光雷达"})
        assert p["request"]["context"] == "前文：用户在讨论激光雷达"
        # 默认字段保留
        assert p["request"]["model_name"] == "bigmodel"
        assert p["request"]["show_utterances"] is True

    def test_extra_request_can_override_default(self) -> None:
        p = _handshake_payload({"model_name": "other", "enable_itn": False})
        assert p["request"]["model_name"] == "other"
        assert p["request"]["enable_itn"] is False

    def test_audio_section_independent(self) -> None:
        p = _handshake_payload({"context": "x"})
        assert p["audio"]["rate"] == 16000
        assert "context" not in p["audio"]


class TestBuildAudioOnlyRequest:
    def test_normal_frame_flags_zero(self) -> None:
        frame = build_audio_only_request(b"abc", is_last=False)
        assert frame[1] == 0x20

    def test_last_frame_sets_eof_bit(self) -> None:
        frame = build_audio_only_request(b"", is_last=True)
        assert frame[1] == 0x22

    def test_payload_is_gzipped(self) -> None:
        frame = build_audio_only_request(b"\x01\x02\x03")
        size = struct.unpack(">I", frame[4:8])[0]
        assert gzip.decompress(frame[8 : 8 + size]) == b"\x01\x02\x03"


class TestParseServerFrame:
    def test_returns_none_on_too_short(self) -> None:
        assert parse_server_frame(b"\x00\x00") is None

    def test_returns_none_on_empty(self) -> None:
        frame = _server_frame({"result": {"text": "", "utterances": []}})
        assert parse_server_frame(frame) is None

    def test_parses_partial(self) -> None:
        frame = _server_frame(
            {
                "result": {
                    "text": "你好",
                    "utterances": [
                        {"text": "你好", "definite": False, "start_time": 100}
                    ],
                }
            }
        )
        evt = parse_server_frame(frame)
        assert evt == TranscriptEvent(
            utterances=(Utterance(text="你好", definite=False, start_time=100),)
        )

    def test_parses_definite(self) -> None:
        frame = _server_frame(
            {
                "result": {
                    "text": "你好世界",
                    "utterances": [
                        {"text": "你好世界", "definite": True, "start_time": 200}
                    ],
                }
            }
        )
        evt = parse_server_frame(frame)
        assert evt is not None
        assert evt.utterances == (
            Utterance(text="你好世界", definite=True, start_time=200),
        )

    def test_skips_empty_partial_placeholder(self) -> None:
        """豆包切分时会塞一个空 partial placeholder——不应被当成有效 utterance。

        bug 复现帧：utterances=[{"text":"前句","definite":True}, {"text":"","definite":False}]
        若把空 placeholder 也算上，会被 session 误当成"当前 partial 为空"，导致
        finalized 没机会保留。
        """
        frame = _server_frame(
            {
                "result": {
                    "text": "前句",
                    "utterances": [
                        {"text": "前句", "definite": True, "start_time": 100},
                        {"text": "", "definite": False, "start_time": 200},
                    ],
                }
            }
        )
        evt = parse_server_frame(frame)
        assert evt is not None
        assert evt.utterances == (
            Utterance(text="前句", definite=True, start_time=100),
        )

    def test_handles_sequence_prefix(self) -> None:
        frame = _server_frame(
            {
                "result": {
                    "text": "测试",
                    "utterances": [
                        {"text": "测试", "definite": True, "start_time": 0}
                    ],
                }
            },
            with_seq=True,
        )
        evt = parse_server_frame(frame)
        assert evt is not None
        assert evt.utterances[0].text == "测试"
        assert evt.utterances[0].definite

    def test_falls_back_to_result_text_when_utterances_empty(self) -> None:
        """有些消息只有 result.text，没有 utterances；应当兜底返回单个 partial。"""
        frame = _server_frame({"result": {"text": "fallback", "utterances": []}})
        evt = parse_server_frame(frame)
        assert evt is not None
        assert evt.utterances == (
            Utterance(text="fallback", definite=False, start_time=0),
        )

    def test_returns_none_on_invalid_json(self) -> None:
        body = gzip.compress(b"not json")
        frame = bytes([0x11, 0x90, 0x11, 0x00]) + struct.pack(">I", len(body)) + body
        assert parse_server_frame(frame) is None


@pytest.mark.parametrize(
    "is_last, expected_byte",
    [(False, 0x20), (True, 0x22)],
)
def test_audio_flag_table(is_last: bool, expected_byte: int) -> None:
    frame = build_audio_only_request(b"x", is_last=is_last)
    assert frame[1] == expected_byte
