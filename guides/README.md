# Provider setup guides

Outly needs a YouTube Data API v3 key for discovery. An OpenAI-compatible LLM
key is needed for scoring and most generative features. All other integrations
are optional.

| Provider | Guide | Key needed |
|---|---|---|
| YouTube Data API | [youtube-studio.md](youtube-studio.md) | API key; OAuth only for Studio |
| Gemini | [gemini.md](gemini.md) | Free-tier key |
| Groq | [groq.md](groq.md) | API key |
| Mistral | [mistral.md](mistral.md) | API key |
| Ollama Cloud | [ollama-cloud.md](ollama-cloud.md) | API key |
| Ollama local | [ollama-local.md](ollama-local.md) | None |

After editing `.env`, run:

```bash
python -m outly doctor --probe
```

The probe makes one real YouTube request and lists the LLM models available to
your key. It costs a small amount of quota/credit. Never paste a key into an
issue, a screenshot, or a commit.
