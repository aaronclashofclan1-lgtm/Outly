# Groq setup

1. Create an account at [console.groq.com](https://console.groq.com/).
2. Create an API key.
3. Configure:
   ```text
   LLM_BASE_URL=https://api.groq.com/openai/v1
   LLM_MODEL=openai/gpt-oss-20b
   LLM_API_KEY=your_key
   ```
4. Run `python -m outly doctor --probe` and use the exact available model name.

Groq is useful as a fallback when the primary provider is rate-limited. To
configure it as fallback, use `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_MODEL`, and
`LLM_FALLBACK_API_KEY`. Never commit the key.
