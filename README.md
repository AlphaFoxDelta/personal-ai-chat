# Personal AI Chat

Something I am playing with. This is my own AI chat app. It runs on my machine, talks to a free local model, and costs nothing to use. I built it because I wanted a chat system I control, with my own branding, and no subscription.

## Why local and free

The default setup uses Ollama, which runs open models right on your computer. No API key, no per-message cost, no data leaving your machine. If you ever want a paid model instead, the config lets you point it at any OpenAI-compatible API.

## Quickstart

1. Install Ollama from https://ollama.com and start it.
2. Pull a model: `ollama pull llama3.1`
3. Install the Python dependencies: `pip install -r requirements.txt`
4. Copy the example config: `cp config.example.yaml config.yaml`
5. Run it: `python app.py`
6. Open http://localhost:5000 in your browser.

If Ollama is not running when you send a message, the app tells you plainly in the chat instead of crashing. Start Ollama and try again.

## What it does

- Chat with streaming responses, so replies appear as they generate.
- Multiple conversations with a sidebar. Start new ones, switch between them, delete the ones you do not need.
- History saved locally in SQLite, so your chats survive restarts.
- Branding and persona set in one config file: app name, accent color, and the system prompt that defines how the assistant behaves.
- Swappable backend: Ollama for free local use, or any OpenAI-compatible API if you add a key later.

## Config options

Everything lives in `config.yaml` (copy it from `config.example.yaml`). The real `config.yaml` is gitignored so keys never get committed.

- `app_name`: name shown in the header and browser tab.
- `accent_color`: highlight color for buttons and your messages.
- `system_prompt`: the persona and behavior instructions for the assistant.
- `backend`: `ollama` or `openai_compatible`.
- `ollama.base_url` / `ollama.model`: where Ollama listens and which model to use.
- `openai_compatible.base_url` / `api_key` / `model`: endpoint, key, and model for a paid API.
- `database`: SQLite file for chat history.
- `port`: port for the web UI.

## Project structure

- `app.py`: Flask server, API routes, SQLite history, streaming responses.
- `backends.py`: model backends. Ollama and OpenAI-compatible, with clean error handling.
- `config.yaml`: your local settings (gitignored).
- `config.example.yaml`: template to copy from.
- `requirements.txt`: Python dependencies.
- `templates/index.html`: the chat page.
- `static/style.css`: styling.
- `static/app.js`: sidebar, streaming, and chat logic in the browser.

## Roadmap

- Agent tools: let the assistant read files, run commands, and search the web, so it can do work and not just chat.
- File uploads: drop in a document and ask questions about it.
- Export: download a conversation as text or markdown.
- Optional auth: a simple login if you ever expose it past localhost.

## License

MIT. Do what you want with it.
