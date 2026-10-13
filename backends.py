"""Model backends for the personal AI chat app.

Two backends are supported:

- OllamaBackend: talks to a local Ollama server (free, no API key).
- OpenAICompatibleBackend: talks to any OpenAI-compatible chat completions
  endpoint (base_url + api_key + model), for paid APIs later.

Both expose a single streaming interface: stream(messages) yields text
chunks. Connection and auth problems raise BackendError with a
human-readable message instead of a traceback.
"""

import json

import requests


class BackendError(Exception):
    """Raised when the model backend cannot fulfill a request."""


class OllamaBackend:
    def __init__(self, base_url, model):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def stream(self, messages):
        url = self.base_url + "/api/chat"
        payload = {"model": self.model, "messages": messages, "stream": True}
        try:
            resp = requests.post(url, json=payload, stream=True, timeout=120)
        except requests.exceptions.ConnectionError:
            raise BackendError(
                "Could not reach Ollama at %s. Is Ollama running? "
                "Start it, then pull a model with: ollama pull %s"
                % (self.base_url, self.model)
            )
        except requests.exceptions.Timeout:
            raise BackendError("Ollama took too long to respond and timed out.")
        if resp.status_code == 404:
            raise BackendError(
                "Ollama responded but the model '%s' was not found. "
                "Pull it with: ollama pull %s" % (self.model, self.model)
            )
        if resp.status_code != 200:
            raise BackendError(
                "Ollama returned HTTP %s: %s"
                % (resp.status_code, resp.text[:200])
            )
        resp.encoding = "utf-8"
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("done"):
                break
            chunk = (data.get("message") or {}).get("content", "")
            if chunk:
                yield chunk
        resp.close()


class OpenAICompatibleBackend:
    def __init__(self, base_url, api_key, model):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def stream(self, messages):
        if not self.api_key:
            raise BackendError(
                "No API key configured. Set openai_compatible.api_key in config.yaml."
            )
        url = self.base_url + "/chat/completions"
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "messages": messages, "stream": True}
        try:
            resp = requests.post(
                url, headers=headers, json=payload, stream=True, timeout=120
            )
        except requests.exceptions.ConnectionError:
            raise BackendError(
                "Could not reach the API at %s. Check base_url in config.yaml."
                % self.base_url
            )
        except requests.exceptions.Timeout:
            raise BackendError("The API took too long to respond and timed out.")
        if resp.status_code == 401:
            raise BackendError(
                "The API rejected the key (HTTP 401). Check api_key in config.yaml."
            )
        if resp.status_code != 200:
            raise BackendError(
                "The API returned HTTP %s: %s"
                % (resp.status_code, resp.text[:200])
            )
        resp.encoding = "utf-8"
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload_text = line[5:].strip()
            if payload_text == "[DONE]":
                break
            try:
                data = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            choices = data.get("choices") or []
            if not choices:
                continue
            chunk = (choices[0].get("delta") or {}).get("content", "")
            if chunk:
                yield chunk
        resp.close()


def build_backend(config):
    """Pick the backend named in config['backend'] and return an instance."""
    which = (config.get("backend") or "ollama").lower()
    if which == "ollama":
        ollama_cfg = config.get("ollama") or {}
        return OllamaBackend(
            base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
            model=ollama_cfg.get("model", "llama3.1"),
        )
    if which in ("openai_compatible", "openai-compatible", "openai"):
        api_cfg = config.get("openai_compatible") or {}
        return OpenAICompatibleBackend(
            base_url=api_cfg.get("base_url", "https://api.openai.com/v1"),
            api_key=api_cfg.get("api_key", ""),
            model=api_cfg.get("model", "gpt-4o-mini"),
        )
    raise BackendError(
        "Unknown backend '%s' in config.yaml. Use 'ollama' or 'openai_compatible'."
        % which
    )
