# Kitsune

My own AI chat app. Runs on my machine, talks to a free local model, costs nothing. I built it because I wanted a chat setup I control, with my own look and no subscription.

## Why local and free

The default setup uses Ollama, which runs open models right on your computer. No API key, no per-message cost, nothing leaves your machine. If you ever want a paid model instead, the config lets you point it at any OpenAI-compatible API.

## Quickstart

1. Install Ollama from https://ollama.com and start it.
2. Pull a model: `ollama pull llama3.1`
3. Install the Python dependencies: `pip install -r requirements.txt`
4. Copy the example config: `cp config.example.yaml config.yaml`
5. Run it: `python app.py`
6. Open http://localhost:5000 in your browser.

If Ollama isn't running when you send a message, the app tells you straight up in the chat instead of blowing up. Start Ollama and try again.

## What it does

- Streaming replies, so text shows up as it generates.
- Multiple conversations in a sidebar. Start new ones, switch between them, delete the ones you don't need.
- History in local SQLite, so chats survive restarts.
- Persona and branding in one config file: name, accent color, and traits that set how the assistant talks. It answers in character instead of sounding like a default chatbot.
- Swappable backend: Ollama for free local use, or any OpenAI-compatible API if you add a key later.
- Avatars: four fox avatars to pick from. The avatar swaps to a focused version while a reply is generating, then swaps back when it's done.
- Profile panel: big avatar, online status, and an activity feed showing your recent chats, searches, and team runs.
- Message actions: hover any message to react, quote-reply, or copy it.
- `/team <task>`: a crew of background workers tackles the task in parallel, then merges their answers into one reply. Every worker gets live web results as background first. Still free, still local.
- `/web <question>`: searches the web and answers from the results, with clickable source links. Follow-ups keep the thread. DuckDuckGo by default (free, no key). On a hosted server, set `TAVILY_API_KEY` and that's tried first, then Google if you set those keys.
- Smart routing: just type. The app figures out whether you need a web search, the team, or a normal reply. Short messages skip the check. Turn it off with `auto_route: false` if you'd rather drive.

## Subagents

A subagent is one background worker: a single model call with its own system prompt, handling one slice of a bigger task. `/team <task>` fans the task out to the whole roster at once. They each answer on their own, then the main model merges everything into one clean reply. If one worker fails, the rest still come through.

The default roster is a researcher, a coder, and a reviewer, under `subagents` in `config.yaml`. Edit that list to build whatever team you want.

There's also a raw endpoint: `POST /api/subagents` with `{"task": "..."}` gives you each worker's answer plus the merged reply.

On Ollama this costs nothing. The tradeoff is speed: the workers share your machine, so a team run takes longer than a normal reply.

## Web search

The local model can't reach the internet on its own, so Kitsune brings the internet to it. `/web <question>` searches, then the model answers from what came back, with sources as clickable links instead of [1][2] markers. If the results don't cover it, it says so instead of making things up.

DuckDuckGo is the default (free, no key). If you're hosting somewhere DuckDuckGo blocks datacenter IPs, set `TAVILY_API_KEY` (1,000 free searches a month, no card, from app.tavily.com) and it's tried first, then Google if `GOOGLE_API_KEY` and `GOOGLE_CX` are set.

This is search-grounded answers, not full browsing. Fast, free, good enough for "what's the latest on X" questions.

## Config

Everything lives in `config.yaml` (copy it from `config.example.yaml`). The real one is gitignored so keys never get committed.

- `app_name`: name in the header and browser tab.
- `accent_color`: highlight color for buttons and your messages.
- `persona`: name, vibe, and traits that shape how the assistant talks. `system_prompt` overrides all of it if you want full manual control.
- `backend`: `ollama` or `openai_compatible`.
- `ollama.base_url` / `ollama.model`: where Ollama listens and which model to use.
- `openai_compatible.base_url` / `api_key` / `model`: endpoint, key, model for a paid API.
- `subagents`: the `/team` roster. Each entry has a `name` and a `system_prompt`.
- `auto_route`: `true` to route messages automatically, `false` for plain chat.
- `tavily_api_key`: optional, for hosted web search.
- `database`: SQLite file for history.
- `port`: port for the web UI.

## Project structure

- `app.py`: Flask server, routes, SQLite history, streaming.
- `backends.py`: Ollama and OpenAI-compatible backends, with errors you can actually read.
- `subagents.py`: parallel workers plus the merge step.
- `webtools.py`: web search with fallbacks, reply formatting cleanup.
- `config.example.yaml`: template. Copy it to `config.yaml` and make it yours.
- `templates/index.html`, `static/style.css`, `static/app.js`: the frontend.
- `static/avatars.js`: the fox avatars, embedded as data URIs.
- `SOUL.md`: the persona definition the app loads at startup.

## Roadmap

- Agent tools: let it read files, run commands, and search the web on its own, so it can do work instead of just chatting.
- File uploads: drop in a doc and ask about it.
- Export a conversation as text or markdown.
- Simple login, if I ever expose it past localhost.

## Hosting it for free

The app reads settings from environment variables, so it runs on free hosts with no code changes:

1. Render free tier: New -> Web Service, connect this repo. The `render.yaml` blueprint pre-fills the build and start commands; you just paste in the model name and API key.
2. Point it at a free model API since hosts can't run Ollama. Set `BACKEND` to `openai_compatible`, `OPENAI_COMPATIBLE_BASE_URL` to your provider, `OPENAI_COMPATIBLE_MODEL` to the model, `OPENAI_COMPATIBLE_API_KEY` to your key.

Env vars it reads: `HOST`, `PORT`, `APP_NAME`, `ACCENT_COLOR`, `SYSTEM_PROMPT`, `PERSONA_NAME`, `PERSONA_VIBE`, `BACKEND`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_MODEL`, `AUTO_ROUTE`, `TAVILY_API_KEY`.

Two free-tier caveats: the app sleeps when idle (takes ~30 seconds to wake up), and SQLite lives on temporary disk, so history resets when the instance restarts.

## License

MIT. Do what you want with it.
