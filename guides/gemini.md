# Gemini setup

1. Open [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Create an API key for a project with the free tier enabled.
3. Put it in `.env`:
   ```text
   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
   LLM_MODEL=gemini-3.5-flash-lite
   LLM_API_KEY=your_key
   ```
4. Run `python -m outly doctor --probe`.
5. If the model name is rejected, copy the exact name listed by the probe or
   your Google account.

Gemini exposes an OpenAI-compatible endpoint for Outly. Free-tier quotas and
model names change; check the provider console. Do not create multiple projects
to multiply YouTube quota. Keep the key out of Git and screenshots.
