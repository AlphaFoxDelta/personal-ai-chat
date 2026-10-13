# Kitsune

Something I'm playing with. Kitsune is my own AI chat app. It runs on my machine, talks to a free local model, and costs nothing to use. I built it because I wanted a chat system I control, with my own branding, and no subscription.

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
- Multiple conversations with a sidebar. Start new ones, switch between them, delete the ones you don't need.
- History saved locally in SQLite, so your chats survive restarts.
- Branding and persona set in one config file: app name, accent color, and the system prompt that defines how the assistant behaves.
- Swappable backend: Ollama for free local use, or any OpenAI-compatible API if you add a key later.
- Subagents: type `/team <task>` in the chat box and a team of background workers tackles it in parallel, then merges their answers into one reply. Still free, still local.
- Web search: type `/web <question>` and Kitsune searches DuckDuckGo (free, no key) and answers using the results.
- Smart routing: just ask. The app figures out whether your message needs a web search, the subagent team, or a normal reply. No commands needed. Web answers come back with clickable source links.

## Subagents

A subagent is a background worker: one model call with its own system prompt, focused on a slice of a bigger task. When you send `/team <task>`, the task is fanned out to every subagent on the roster at once. They each answer independently (every subagent gets live web search results as background), then the main model merges their answers into one clean reply. If one subagent fails, the rest still come through.

The default roster is a researcher, a coder, and a reviewer, defined under `subagents` in `config.yaml`. Edit that list to build your own team with whatever roles and prompts you want.

There's also a raw endpoint if you want to call it yourself: `POST /api/subagents` with `{"task": "..."}` returns each subagent's answer plus the merged reply.

Everything runs against your configured backend, so on Ollama subagents cost nothing. The tradeoff is speed: they share your machine, so a team run takes longer than a normal reply.

## Smart routing

You don't have to remember the commands. Every message is read first and the app decides: web search for current info, the subagent team for big multi-part tasks, or a normal reply for everything else. Short messages skip the check. Turn it off with `auto_route: false` in `config.yaml` if you'd rather drive yourself. `/web` and `/team` still work as explicit overrides.

## Web search

The local model has no internet of its own, so Kitsune brings the internet to it. Type `/web <question>` in the chat box and the app searches DuckDuckGo, free with no API key, then asks the model to answer using the results. If the results do not cover the question, it says so instead of guessing.

This is search-grounded answers, not full browsing: fast, free, and good enough for "what is the latest on X" questions.

## Config options

Everything lives in `config.yaml` (copy it from `config.example.yaml`). The real `config.yaml` is gitignored so keys never get committed.

- `app_name`: name shown in the header and browser tab.
- `accent_color`: highlight color for buttons and your messages.
- `system_prompt`: the persona and behavior instructions for the assistant.
- `backend`: `ollama` or `openai_compatible`.
- `ollama.base_url` / `ollama.model`: where Ollama listens and which model to use.
- `openai_compatible.base_url` / `api_key` / `model`: endpoint, key, and model for a paid API.
- `subagents`: the team roster for `/team`. Each entry has a `name` and a `system_prompt`.
- `auto_route`: `true` to automatically route messages to web search, the team, or normal chat. `false` for plain chat only.
- `database`: SQLite file for chat history.
- `port`: port for the web UI.

## Project structure

- `app.py`: Flask server, API routes, SQLite history, streaming responses.
- `backends.py`: model backends. Ollama and OpenAI-compatible, with clean error handling.
- `subagents.py`: parallel background workers plus the merge step.
- `webtools.py`: DuckDuckGo web search, no API key needed.
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

## Hosting it for free

The app reads settings from environment variables, so it runs on free hosts without changing code. The usual free setup:

1. Put it on Render's free tier: connect this repo, start command `python app.py`.
2. Point it at a free model API instead of local Ollama. OpenRouter has free models: set `BACKEND` to `openai_compatible`, `OPENAI_COMPATIBLE_BASE_URL` to `https://openrouter.ai/api/v1`, `OPENAI_COMPATIBLE_MODEL` to a `:free` model, and `OPENAI_COMPATIBLE_API_KEY` to your free key.

Supported env vars: `HOST`, `PORT`, `APP_NAME`, `ACCENT_COLOR`, `SYSTEM_PROMPT`, `BACKEND`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_MODEL`, `AUTO_ROUTE`.

Two free-tier caveats: the app sleeps when idle and takes ~30 seconds to wake up, and chat history lives in SQLite on temporary disk, so it resets when the instance restarts.

## License

MIT. Do what you want with it.
