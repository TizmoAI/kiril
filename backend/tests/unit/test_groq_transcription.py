from pathlib import Path

import httpx
import pytest

from src.media import transcription


def groq_payload(text, segments, words, language="en"):
    return {"text": text, "language": language, "segments": segments, "words": words}


def test_attach_words_to_segments_groups_by_time():
    segments = [{"start": 0.0, "end": 1.0, "text": "a b"}, {"start": 1.0, "end": 2.0, "text": "c"}]
    words = [
        {"word": "a", "start": 0.0, "end": 0.4},
        {"word": "b", "start": 0.5, "end": 0.9},
        {"word": "c", "start": 1.1, "end": 1.6},
    ]

    nested = transcription._attach_words_to_segments(segments, words)

    assert [w["word"] for w in nested[0]["words"]] == ["a", "b"]
    assert [w["word"] for w in nested[1]["words"]] == ["c"]


def test_chunk_retries_on_429_then_succeeds(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")
        return httpx.Response(200, json=groq_payload("hi", [], []))

    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"audio")
    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = transcription._groq_transcribe_chunk(client, chunk, "key", "whisper-large-v3-turbo", None)

    assert result["text"] == "hi"
    assert len(calls) == 2


def test_chunk_raises_clear_error_when_rate_limit_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(transcription.time, "sleep", lambda _: None)
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"audio")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"retry-after": "0"}))
    )

    with pytest.raises(RuntimeError, match="rate limit"):
        transcription._groq_transcribe_chunk(client, chunk, "key", "whisper-large-v3-turbo", None)


def test_transcribe_with_groq_offsets_chunk_timestamps(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "video.transcription.mp3"
    audio.write_bytes(b"audio")
    chunks = [tmp_path / "chunk_0000.mp3", tmp_path / "chunk_0001.mp3"]
    payloads = [
        groq_payload(
            "first",
            [{"start": 0.0, "end": 2.0, "text": "first"}],
            [{"word": "first", "start": 0.5, "end": 1.0}],
        ),
        groq_payload(
            "second",
            [{"start": 0.0, "end": 2.0, "text": "second"}],
            [{"word": "second", "start": 0.5, "end": 1.0}],
        ),
    ]
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda _: audio)
    monkeypatch.setattr(transcription, "_split_audio_into_chunks", lambda *args: chunks)
    monkeypatch.setattr(
        transcription,
        "_groq_transcribe_chunk",
        lambda client, chunk, api_key, model, language: payloads[chunks.index(chunk)],
    )

    result = transcription.transcribe_with_groq(video, "key", language="en_us")

    assert result["text"] == "first second"
    assert result["language"] == "en"
    second_segment = result["segments"][1]
    assert second_segment["start"] == transcription.GROQ_CHUNK_SECONDS
    assert second_segment["words"][0]["start"] == transcription.GROQ_CHUNK_SECONDS + 0.5

    # The result must flow through the existing Whisper-format converter unchanged.
    data = transcription._whisper_result_to_transcript_data(result)
    assert [w["text"] for w in data["words"]] == ["first", "second"]
    assert data["words"][1]["start"] == int((transcription.GROQ_CHUNK_SECONDS + 0.5) * 1000)


def test_get_transcript_requires_groq_key():
    config = type("C", (), {"groq_api_key": None})()
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        transcription._get_transcript_with_groq(Path("video.mp4"), config)
