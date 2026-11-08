# Mistral setup

1. Create an account at [console.mistral.ai](https://console.mistral.ai/).
2. Create an API key.
3. Configure:
   ```text
   LLM_BASE_URL=https://api.mistral.ai/v1
   LLM_MODEL=ministral-8b-latest
   LLM_API_KEY=your_key
   ```
4. Run `python -m outly doctor --probe`.

Model availability and free-tier limits are shown in the Mistral console.
Choose an instruction-following model that supports JSON output; Outly validates
the response against its schemas and retries once. Do not paste the key into an
issue.
