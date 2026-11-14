"""Transcription helpers for the video pipeline."""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
import assemblyai as aai
from ..config import get_config
import httpx
import json
import re
import shutil
import time
from .common import (
    ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
    ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
    ANALYSIS_SEGMENT_MAX_DURATION_MS,
    ANALYSIS_SEGMENT_MAX_WORDS,
    ANALYSIS_SEGMENT_MIN_WORDS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS,
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
    logger,
)
from .ffmpeg import (
    run_ffmpeg_command,
    select_audio_stream_index,
)


def _prepare_audio_for_transcription(video_path: Path) -> Path:
    """Extract a compact audio-only file for transcription."""
    audio_path = video_path.with_name(f"{video_path.stem}.transcription.mp3")
    if audio_path.exists() and audio_path.stat().st_size > 0:
        return audio_path

    # Transcribe the same track the renderer cuts, not ffmpeg's implicit pick.
    audio_stream = select_audio_stream_index(video_path)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        *(["-map", f"0:a:{audio_stream}"] if audio_stream is not None else []),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    try:
        result = run_ffmpeg_command(command, timeout=900)
    except FileNotFoundError:
        logger.warning(
            "ffmpeg is not available; falling back to source video for transcription"
        )
        return video_path

    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        logger.warning(
            "Failed to extract transcription audio with ffmpeg; falling back to source video"
        )
        return video_path

    logger.info(
        "Prepared transcription audio: %s (%.2f MB)",
        audio_path,
        audio_path.stat().st_size / (1024 * 1024),
    )
    return audio_path


def _submit_and_wait_for_assemblyai_transcript(
    transcriber,
    media_path: Path,
    config_obj,
    timeout_seconds: int,
):
    """Submit a transcript job and poll with a total timeout."""
    # Uploads are safe to retry: they do not create a paid transcript. Keep the
    # transcript POST outside this loop because a timeout may hide its success.
    for attempt in range(1, 4):
        try:
            audio_url = transcriber.upload_file(str(media_path))
            break
        except (httpx.TransportError, aai.AssemblyAIError) as error:
            status = getattr(error, "status_code", None)
            retryable = isinstance(error, httpx.TransportError) or status in {408, 429, 500, 502, 503, 504}
            if not retryable:
                raise
            if attempt == 3:
                raise RuntimeError(
                    "AssemblyAI audio upload unavailable after 3 attempts"
                    + (f" (HTTP {status})." if status else ".")
                ) from None
            logger.warning("AssemblyAI audio upload failed (HTTP %s); retrying upload (%s/3)", status, attempt)
            time.sleep(2.0 ** attempt)
    submitted = transcriber.submit(audio_url, config=config_obj)
    if not submitted.id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")

    logger.info("AssemblyAI transcript submitted: %s", submitted.id)
    deadline = time.monotonic() + timeout_seconds
    next_log_at = 0.0
    consecutive_failures = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"AssemblyAI transcript {submitted.id} did not complete within {timeout_seconds}s"
            )
        # The SDK poller uses the upload timeout (often 900s) for every GET.
        # Bound each poll without changing the shared client's timeout.
        try:
            response = submitted._client.http_client.get(  # noqa: SLF001
                f"{aai.api.ENDPOINT_TRANSCRIPT}/{submitted.id}",
                timeout=min(30.0, remaining),
            )
            response.raise_for_status()
            response = aai.types.TranscriptResponse(**response.json())
            consecutive_failures = 0
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise
            consecutive_failures += 1
            logger.warning("AssemblyAI status polling unavailable; retrying the existing transcript")
            time.sleep(min(30.0, 2.0 ** min(consecutive_failures, 4),
                           max(0.0, deadline - time.monotonic())))
            continue
        transcript = aai.Transcript.from_response(
            client=submitted._client,  # noqa: SLF001
            response=response,
        )

        if transcript.status in (
            aai.TranscriptStatus.completed,
            aai.TranscriptStatus.error,
        ):
            return transcript

        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(
                f"AssemblyAI transcript {submitted.id} did not complete within {timeout_seconds}s"
            )

        if now >= next_log_at:
            logger.info(
                "AssemblyAI transcript %s still %s",
                submitted.id,
                transcript.status,
            )
            next_log_at = now + 30

        time.sleep(min(aai.settings.polling_interval, max(0.0, deadline - now)))


def _assemblyai_speech_models_value(speech_model: str) -> List[str]:
    """Prefer Universal-3.5 Pro; retain Universal-2 for unsupported languages.

    Explicit low-cost aliases remain available for self-hosted configurations.
    Legacy quality aliases upgrade to the current Pro model.
    """
    normalized = (speech_model or "universal-3-5-pro").strip().lower()
    if normalized in {"nano", "universal-2"}:
        return ["universal-2"]
    return ["universal-3-5-pro", "universal-2"]


def _whisper_result_to_transcript_data(whisper_result: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Whisper result dict to the standard transcript-cache format.

    Whisper reports timings in seconds; the cache (and AssemblyAI path) stores
    milliseconds, so each timestamp is multiplied by 1000 here.
    """
    words_data: List[Dict[str, Any]] = []
    utterances_data: List[Dict[str, Any]] = []

    for segment in whisper_result.get("segments") or []:
        seg_words = [
            {
                "text": w.get("word", w.get("text", "")),
                "start": int(w["start"] * 1000) if isinstance(w.get("start"), float) else int(w.get("start", 0)),
                "end": int(w["end"] * 1000) if isinstance(w.get("end"), float) else int(w.get("end", 0)),
                "confidence": w.get("probability", w.get("confidence", 1.0)),
                "speaker": None,
            }
            for w in segment.get("words") or []
        ]
        utterances_data.append(
            {
                "text": segment.get("text", ""),
                "start": int(segment["start"] * 1000) if "start" in segment else 0,
                "end": int(segment["end"] * 1000) if "end" in segment else 0,
                "speaker": None,
                "words": seg_words,
            }
        )
        words_data.extend(seg_words)

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": whisper_result.get("text", ""),
        "language_code": whisper_result.get("language"),
    }


GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# 64 kbps mono mp3 is ~4.8 MB per 10 minutes, well under Groq's 25 MB upload cap.
GROQ_CHUNK_SECONDS = 600
GROQ_MAX_ATTEMPTS = 5


def _split_audio_into_chunks(audio_path: Path, chunk_dir: Path, chunk_seconds: int) -> List[Path]:
    """Split an audio file into fixed-length chunks without re-encoding."""
    chunk_dir.mkdir(parents=True, exist_ok=True)
    pattern = chunk_dir / f"chunk_%04d{audio_path.suffix}"
    result = run_ffmpeg_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-c",
            "copy",
            str(pattern),
        ],
        timeout=900,
    )
    chunks = sorted(chunk_dir.glob(f"chunk_*{audio_path.suffix}"))
    if result.returncode != 0 or not chunks:
        raise RuntimeError("Failed to split audio into chunks for Groq transcription")
    return chunks


def _groq_transcribe_chunk(
    client: httpx.Client,
    chunk_path: Path,
    api_key: str,
    model_name: str,
    language: Optional[str],
) -> Dict[str, Any]:
    """Transcribe one chunk, retrying on rate limits and transient server errors."""
    data: Dict[str, Any] = {
        "model": model_name,
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["word", "segment"],
    }
    if language:
        data["language"] = language

    for attempt in range(1, GROQ_MAX_ATTEMPTS + 1):
        with open(chunk_path, "rb") as audio_file:
            response = client.post(
                GROQ_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files={"file": (chunk_path.name, audio_file, "audio/mpeg")},
            )
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503, 504) and attempt < GROQ_MAX_ATTEMPTS:
            try:
                delay = float(response.headers.get("retry-after", ""))
            except ValueError:
                delay = 2.0**attempt
            logger.warning(
                "Groq transcription returned %s for %s; retrying in %.1fs (attempt %d/%d)",
                response.status_code,
                chunk_path.name,
                delay,
                attempt,
                GROQ_MAX_ATTEMPTS,
            )
            time.sleep(delay)
            continue
        if response.status_code == 429:
            raise RuntimeError(
                "Groq transcription rate limit reached (free-tier quota). Try again later."
            )
        raise RuntimeError(
            f"Groq transcription failed ({response.status_code}): {response.text[:500]}"
        )
    raise RuntimeError("Groq transcription failed after retries")


def _attach_words_to_segments(
    segments: List[Dict[str, Any]], words: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Groq returns words at the top level; the transcript format nests them in segments."""
    nested = [{**segment, "words": []} for segment in segments]
    if not nested:
        return nested
    index = 0
    for word in words:
        midpoint = (float(word["start"]) + float(word["end"])) / 2
        while index < len(nested) - 1 and midpoint >= float(nested[index]["end"]):
            index += 1
        nested[index]["words"].append(word)
    return nested


def transcribe_with_groq(
    video_path: Path,
    api_key: str,
    model_name: str = "whisper-large-v3-turbo",
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Transcribe via Groq's hosted Whisper, returning a local-Whisper-shaped result.

    Audio is split into chunks to stay under Groq's upload limit, and each chunk's
    timestamps (seconds) are shifted by the chunk's start offset.
    """
    audio_path = _prepare_audio_for_transcription(video_path)
    if audio_path == video_path:
        raise RuntimeError("ffmpeg is required to prepare audio for Groq transcription")

    groq_language = language.split("_", 1)[0] if language else None
    chunk_dir = audio_path.with_name(f"{audio_path.stem}.groq_chunks")
    segments: List[Dict[str, Any]] = []
    texts: List[str] = []
    detected_language: Optional[str] = None
    try:
        chunks = _split_audio_into_chunks(audio_path, chunk_dir, GROQ_CHUNK_SECONDS)
        logger.info(
            "Starting Groq transcription with model: %s (%d chunks, language: %s)",
            model_name,
            len(chunks),
            groq_language or "auto",
        )
        with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
            for chunk_index, chunk_path in enumerate(chunks):
                offset = chunk_index * GROQ_CHUNK_SECONDS
                payload = _groq_transcribe_chunk(
                    client, chunk_path, api_key, model_name, groq_language
                )
                detected_language = detected_language or payload.get("language")
                shifted_words = [
                    {
                        **word,
                        "start": float(word["start"]) + offset,
                        "end": float(word["end"]) + offset,
                    }
                    for word in payload.get("words") or []
                ]
                shifted_segments = [
                    {
                        **segment,
                        "start": float(segment["start"]) + offset,
                        "end": float(segment["end"]) + offset,
                    }
                    for segment in payload.get("segments") or []
                ]
                segments.extend(_attach_words_to_segments(shifted_segments, shifted_words))
                texts.append((payload.get("text") or "").strip())
    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)

    return {
        "text": " ".join(text for text in texts if text),
        "segments": segments,
        "language": detected_language,
    }


def transcribe_with_youtube_captions(video_url: str) -> Optional[str]:
    """Extract a plain-text transcript from a YouTube video's captions via yt-dlp.

    Only valid for YouTube-sourced videos. Returns plain text without word-level
    timestamps, so subtitle generation is not supported on this path.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp is required for YouTube caption extraction")
        return None

    video_id = None
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", video_url)
    if match:
        video_id = match.group(1)

    if not video_id:
        logger.error("Could not extract YouTube video ID from URL: %s", video_url)
        return None

    temp_dir = Path(get_config().temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    subs_path = temp_dir / f"{video_id}.en.vtt"

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "vtt",
            "skip_download": True,
            "outtmpl": str(temp_dir / video_id),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        if subs_path.exists():
            text = subs_path.read_text(encoding="utf-8")
            lines = []
            for line in text.splitlines():
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("WEBVTT")
                    and not stripped.startswith("Kind:")
                    and not stripped.startswith("Language:")
                    and "-->" not in line
                    and not stripped.startswith("NOTE")
                    and not re.match(r"^\d+$", stripped)
                ):
                    lines.append(stripped)
            return " ".join(lines)

        logger.warning("No English captions found for video %s", video_id)
        return None

    except Exception as e:
        logger.error("Failed to extract YouTube captions: %s", e)
        return None
    finally:
        for f in temp_dir.glob(f"{video_id}.*"):
            if f.suffix in (".vtt", ".srt", ".ttml", ".json"):
                try:
                    f.unlink()
                except OSError:
                    pass


def get_video_transcript(
    video_path: Path,
    speech_model: str = "universal",
    source_url: Optional[str] = None,
) -> str:
    """Get a video transcript using the configured provider.

    Dispatches to Groq, AssemblyAI, or YouTube captions based on
    ``TRANSCRIPTION_PROVIDER``. ``source_url`` enables the youtube_captions
    provider, which needs the original URL rather than a local file path.
    """
    logger.info(f"Getting transcript for: {video_path}")
    runtime_config = get_config()
    provider = runtime_config.transcription_provider

    if provider == "groq":
        return _get_transcript_with_groq(video_path, runtime_config)
    if provider == "youtube_captions":
        if not source_url:
            raise ValueError(
                "youtube_captions provider requires a YouTube URL. "
                "Pass source_url to get_video_transcript()."
            )
        return _get_transcript_with_youtube_captions(source_url)
    return _get_transcript_with_assemblyai(video_path, speech_model, runtime_config)


def _get_transcript_with_assemblyai(
    video_path: Path, speech_model: str, runtime_config
) -> str:
    """Get transcript using AssemblyAI with word-level timing for precise subtitles."""
    # A job owns its client: concurrent jobs must not mutate shared SDK settings.
    client = aai.Client(settings=aai.Settings(
        api_key=runtime_config.assembly_ai_api_key,
        base_url=aai.settings.base_url,
        http_timeout=getattr(runtime_config, "assembly_ai_request_timeout_seconds", 60),
    ))
    transcriber = aai.Transcriber(client=client)

    # AssemblyAI now requires the plural `speech_models` list; the singular
    # `best`/`nano`/`universal` values were deprecated server-side.
    selected_model = getattr(runtime_config, "assembly_ai_speech_model", "") or speech_model
    speech_models_value = _assemblyai_speech_models_value(selected_model)

    # Without a language, AssemblyAI assumes US English and garbles everything
    # else; detection also routes to the first listed model that supports it.
    language = runtime_config.transcription_language
    config_obj = aai.TranscriptionConfig(
        speaker_labels=True,
        punctuate=True,
        format_text=True,
        speech_models=speech_models_value,
        language_code=language or None,
        language_detection=None if language else True,
    )

    try:
        logger.info("Starting AssemblyAI transcription")
        transcription_media_path = _prepare_audio_for_transcription(video_path)
        # Once submitted, a polling timeout must not submit another paid job or
        # restart the full transcription budget. Retry status GETs above instead.
        transcript = _submit_and_wait_for_assemblyai_transcript(
            transcriber,
            transcription_media_path,
            config_obj,
            runtime_config.assembly_ai_http_timeout_seconds,
        )

        if transcript is None:
            raise RuntimeError("AssemblyAI transcription did not return a transcript")

        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AssemblyAI transcription failed: {transcript.error}")
            raise Exception(f"Transcription failed: {transcript.error}")

        response = getattr(transcript, "json_response", None) or {}
        logger.info(
            "AssemblyAI transcript language: %s (confidence: %s)",
            response.get("language_code") or language or "unknown",
            response.get("language_confidence"),
        )

        formatted_lines = format_transcript_for_analysis(transcript)
        cache_transcript_data(video_path, transcript)

        result = "\n".join(formatted_lines)
        logger.info(
            f"Transcript formatted: {len(formatted_lines)} segments, {len(result)} chars"
        )
        return result

    except Exception as e:
        logger.error(f"Error in AssemblyAI transcription: {e}")
        raise
    finally:
        client.http_client.close()


def _get_transcript_with_groq(video_path: Path, runtime_config) -> str:
    """Get transcript using Groq-hosted Whisper with word-level timestamps."""
    if not runtime_config.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is required when TRANSCRIPTION_PROVIDER=groq")
    groq_result = transcribe_with_groq(
        video_path,
        runtime_config.groq_api_key,
        runtime_config.groq_whisper_model,
        runtime_config.transcription_language or None,
    )

    formatted_lines = format_transcript_for_analysis(groq_result)
    cache_transcript_data(video_path, groq_result)

    result = "\n".join(formatted_lines)
    logger.info(
        "Groq transcript formatted: %d segments, %d chars",
        len(formatted_lines),
        len(result),
    )
    return result


def _get_transcript_with_youtube_captions(source_url: str) -> str:
    """Get transcript from YouTube captions (plain text, no word timings)."""
    logger.info("Extracting YouTube captions for: %s", source_url)
    transcript = transcribe_with_youtube_captions(source_url)
    if not transcript:
        raise RuntimeError(
            "YouTube caption extraction failed or returned no captions. "
            "Falling back requires a different TRANSCRIPTION_PROVIDER."
        )
    logger.info("YouTube caption transcript: %d chars", len(transcript))
    return transcript


def cache_transcript_data(video_path: Path, transcript) -> None:
    """Cache transcript data for subtitle generation.

    Handles both AssemblyAI transcript objects and Whisper result dicts.
    """
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if isinstance(transcript, dict):
        cache_data = _whisper_result_to_transcript_data(transcript)
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)
        logger.info("Cached %d words to %s", len(cache_data["words"]), cache_path)
        return

    words_data = []
    if transcript.words:
        words_data = [_serialize_transcript_word(word) for word in transcript.words]

    utterances_data = []
    if getattr(transcript, "utterances", None):
        utterances_data = [
            {
                "text": utterance.text,
                "start": utterance.start,
                "end": utterance.end,
                "speaker": getattr(utterance, "speaker", None),
                "words": [
                    _serialize_transcript_word(word)
                    for word in getattr(utterance, "words", []) or []
                ],
            }
            for utterance in transcript.utterances
        ]

    response = getattr(transcript, "json_response", None)
    language_code = response.get("language_code") if isinstance(response, dict) else None
    if not language_code:
        language_code = getattr(getattr(transcript, "config", None), "language_code", None)

    cache_data = {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": transcript.text,
        # Lets language-specific cleanup (e.g. default filler words) avoid
        # cutting ordinary words in other languages.
        "language_code": language_code if isinstance(language_code, str) else None,
    }

    with open(cache_path, "w") as f:
        json.dump(cache_data, f)

    logger.info(f"Cached {len(words_data)} words to {cache_path}")


def load_cached_transcript_data(video_path: Path) -> Optional[Dict]:
    """Load cached AssemblyAI transcript data."""
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r") as f:
            payload = json.load(f)
            if "version" not in payload:
                payload["version"] = TRANSCRIPT_CACHE_SCHEMA_VERSION
                payload.setdefault("utterances", [])
            return payload
    except Exception as e:
        logger.warning(f"Failed to load transcript cache: {e}")
        return None


def _serialize_transcript_word(word) -> Dict[str, Any]:
    if isinstance(word, dict):
        return {
            "text": word.get("word", word.get("text", "")),
            "start": int(word["start"] * 1000) if isinstance(word.get("start"), float) else int(word.get("start", 0)),
            "end": int(word["end"] * 1000) if isinstance(word.get("end"), float) else int(word.get("end", 0)),
            "confidence": word.get("probability", word.get("confidence", 1.0)),
            "speaker": word.get("speaker"),
        }
    return {
        "text": word.text,
        "start": word.start,
        "end": word.end,
        "confidence": word.confidence if hasattr(word, "confidence") else 1.0,
        "speaker": getattr(word, "speaker", None),
    }


def _join_transcript_tokens(tokens: List[str]) -> str:
    text = " ".join(token.strip() for token in tokens if token and token.strip())
    for before, after in (
        (" ,", ","),
        (" .", "."),
        (" !", "!"),
        (" ?", "?"),
        (" ;", ";"),
        (" :", ":"),
        (" n't", "n't"),
        (" 're", "'re"),
        (" 've", "'ve"),
        (" 'll", "'ll"),
        (" 'd", "'d"),
        (" 'm", "'m"),
        (" 's", "'s"),
    ):
        text = text.replace(before, after)
    return text.strip()


def _format_words_for_analysis(
    words: List[Any],
    speaker: Optional[str] = None,
    *,
    min_words_per_segment: int = ANALYSIS_SEGMENT_MIN_WORDS,
    max_words_per_segment: int = ANALYSIS_SEGMENT_MAX_WORDS,
    max_duration_ms: int = ANALYSIS_SEGMENT_MAX_DURATION_MS,
) -> List[str]:
    if not words:
        return []

    formatted_lines: List[str] = []
    current_words: List[Any] = []
    current_start: Optional[int] = None

    def flush_segment() -> None:
        nonlocal current_words, current_start
        if not current_words:
            return
        start_time = format_ms_to_timestamp(current_words[0].start)
        end_time = format_ms_to_timestamp(current_words[-1].end)
        text = _join_transcript_tokens([word.text for word in current_words])
        if text:
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(f"[{start_time} - {end_time}] {speaker_prefix}{text}")
        current_words = []
        current_start = None

    for word in words:
        if current_start is None:
            current_start = word.start

        current_words.append(word)
        duration_ms = word.end - current_start
        segment_word_count = len(current_words)
        ends_sentence = str(word.text).endswith((".", "!", "?"))

        should_flush = False
        if segment_word_count >= max_words_per_segment:
            should_flush = True
        elif (
            ends_sentence
            and segment_word_count >= min_words_per_segment
        ):
            should_flush = True
        elif (
            duration_ms >= max_duration_ms
            and segment_word_count >= min_words_per_segment
        ):
            should_flush = True

        if should_flush:
            flush_segment()

    flush_segment()
    return formatted_lines


def format_transcript_for_analysis(transcript) -> List[str]:
    """Format transcripts into readable timestamped segments for AI analysis.

    Handles both AssemblyAI transcript objects (utterances/words with ms
    timings) and Whisper result dicts (segments with second-based timings).
    """
    # Whisper result dict: treat each segment as an utterance, converting the
    # second-based timings to milliseconds to match the timestamp formatter.
    if isinstance(transcript, dict):
        formatted_lines = []
        for segment in transcript.get("segments") or []:
            start_ms = int(segment.get("start", 0) * 1000)
            end_ms = int(segment.get("end", 0) * 1000)
            formatted_lines.append(
                f"[{format_ms_to_timestamp(start_ms)} - {format_ms_to_timestamp(end_ms)}] "
                f"{segment.get('text', '').strip()}"
            )
        return formatted_lines

    utterances = getattr(transcript, "utterances", None) or []
    if utterances:
        formatted_lines = []
        for utterance in utterances:
            utterance_words = list(getattr(utterance, "words", []) or [])
            utterance_duration = max(0, int(utterance.end) - int(utterance.start))
            if utterance_words and (
                utterance_duration > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS
                or len(utterance_words) > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS
            ):
                formatted_lines.extend(
                    _format_words_for_analysis(
                        utterance_words,
                        getattr(utterance, "speaker", None),
                        max_words_per_segment=ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
                        max_duration_ms=ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
                    )
                )
                continue

            start_time = format_ms_to_timestamp(utterance.start)
            end_time = format_ms_to_timestamp(utterance.end)
            speaker = getattr(utterance, "speaker", None)
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(
                f"[{start_time} - {end_time}] {speaker_prefix}{utterance.text}"
            )
        return formatted_lines

    formatted_lines = []
    words = getattr(transcript, "words", None) or []
    if not words:
        return formatted_lines

    logger.info(f"Processing {len(words)} words with precise timing")
    return _format_words_for_analysis(words)


def format_ms_to_timestamp(ms: int) -> str:
    """Format milliseconds to MM:SS format."""
    seconds = ms // 1000
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"
