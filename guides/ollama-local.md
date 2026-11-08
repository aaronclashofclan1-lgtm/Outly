# Ollama local setup

1. Install [Ollama](https://ollama.com/download).
2. Start Ollama and pull a model:
   ```bash
   ollama pull gpt-oss:20b
   ```
3. Configure:
   ```text
   LLM_BASE_URL=http://localhost:11434/v1
   LLM_MODEL=gpt-oss:20b
   LLM_API_KEY=
   ```
4. Run `python -m outly doctor --probe`.

No cloud key is needed. Local inference uses your CPU/GPU and can be slow on
long contexts. If the port is not `11434`, change the base URL. Do not expose
the local Ollama server to the public internet.
