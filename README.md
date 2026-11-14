# Kiril

Turn long videos into vertical short clips built around their most viral moments.

Paste a YouTube link (or upload a file). Kiril transcribes it, uses Claude to pick and score the best moments, then renders 9:16 clips with face-centered framing, animated captions and a hook title. A built-in editor lets you trim, split and re-export each clip.

## How it works

| Step | Tech |
|---|---|
| Transcription | Groq-hosted Whisper (word-level timestamps) |
| Clip selection and scoring | Claude (`claude-sonnet-5-5`) |
| Framing | Face detection (OpenCV) |
| Rendering | ffmpeg |
| App | Next.js frontend, FastAPI backend, ARQ workers, PostgreSQL, Redis |

## Run it

Everything runs in Docker, so you need nothing installed besides Docker (with Compose).

```bash
cp .env.example .env
# Set ANTHROPIC_API_KEY and GROQ_API_KEY in .env (Groq has a free tier)
# Also set BACKEND_AUTH_SECRET, BETTER_AUTH_SECRET and REDIS_PASSWORD to random values
docker compose up -d --build
```

- App: http://localhost:3107
- API docs: http://localhost:8000/docs

Stop with `docker compose down`. Logs: `docker compose logs -f backend worker`.

## Configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude, used for clip selection and scoring |
| `GROQ_API_KEY` | Whisper transcription |
| `LLM` | Override the model, e.g. `anthropic:claude-sonnet-5-5` |
| `TRANSCRIPTION_PROVIDER` | `groq` (default), `assemblyai` or `youtube_captions` |
| `BACKEND_PLATFORM` | `linux/arm64` on Apple Silicon, `linux/amd64` otherwise |

See [docs/configuration.md](docs/configuration.md) for the full list and [docs/](docs/) for architecture, the editor and API reference.

## Tests

```bash
docker compose exec backend .venv/bin/pytest --no-cov
```

Frontend: `pnpm run lint && pnpm run typecheck && pnpm run test` inside `frontend/`.

## License

AGPL-3.0, see [LICENSE](LICENSE).
