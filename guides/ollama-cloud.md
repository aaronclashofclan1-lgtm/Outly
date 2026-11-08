# Ollama Cloud setup

1. Create an account at [ollama.com](https://ollama.com/).
2. Open **Settings -> Keys** and create an API key.
3. Configure:
   ```text
   LLM_BASE_URL=https://ollama.com/v1
   LLM_MODEL=gemma4:31b
   LLM_API_KEY=your_key
   ```
4. Run `python -m outly doctor --probe`.
5. Use the exact model name shown in your Ollama Cloud account.

Ollama Cloud exposes an OpenAI-compatible endpoint. It can be the primary or
fallback provider. Keep the key in `.env`.
