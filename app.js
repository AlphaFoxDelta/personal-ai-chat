// Kitsune frontend: sidebar conversations, streaming chat,
// the /team command (subagents), and the /web command (web search).

let currentConversationId = null;
let isStreaming = false;

const messagesEl = document.getElementById("messages");
const errorBar = document.getElementById("error-bar");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const conversationListEl = document.getElementById("conversation-list");
const newChatBtn = document.getElementById("new-chat");

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || ("Request failed: " + res.status));
  }
  return data;
}

function showError(text) {
  errorBar.textContent = text;
  errorBar.classList.remove("hidden");
}

function clearError() {
  errorBar.textContent = "";
  errorBar.classList.add("hidden");
}

// Render message text as HTML: escape everything, then turn markdown
// links [text](url) and bare URLs into clickable links.
function renderRich(text) {
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const links = [];
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (m, t, u) => {
    links.push('<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + t + "</a>");
    return "\u0000" + (links.length - 1) + "\u0000";
  });
  html = html.replace(/(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  html = html.replace(/\u0000(\d+)\u0000/g, (m, i) => links[Number(i)]);
  return html;
}

function addMessage(role, text, streaming) {
  const div = document.createElement("div");
  div.className = "message " + role + (streaming ? " streaming" : "");
  div.innerHTML = renderRich(text);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

async function loadBranding() {
  try {
    const cfg = await api("/api/config");
    document.getElementById("app-name").textContent = cfg.app_name;
    document.getElementById("page-title").textContent = cfg.app_name;
    document.documentElement.style.setProperty("--accent", cfg.accent_color);
  } catch (e) {
    // Branding is cosmetic, keep defaults on failure.
  }
}

async function loadConversations() {
  const conversations = await api("/api/conversations");
  conversationListEl.innerHTML = "";
  for (const conv of conversations) {
    const item = document.createElement("div");
    item.className =
      "conversation-item" +
      (conv.id === currentConversationId ? " active" : "");
    const title = document.createElement("span");
    title.className = "conversation-title";
    title.textContent = conv.title;
    title.title = conv.title;
    const del = document.createElement("button");
    del.className = "delete-btn";
    del.textContent = "×";
    del.title = "Delete";
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("Delete this conversation?")) return;
      await api("/api/conversations/" + conv.id, { method: "DELETE" });
      if (conv.id === currentConversationId) {
        currentConversationId = null;
        messagesEl.innerHTML = "";
      }
      await loadConversations();
      if (!currentConversationId) {
        await createConversation();
      }
    });
    item.appendChild(title);
    item.appendChild(del);
    item.addEventListener("click", () => selectConversation(conv.id));
    conversationListEl.appendChild(item);
  }
}

async function createConversation() {
  const conv = await api("/api/conversations", { method: "POST" });
  currentConversationId = conv.id;
  messagesEl.innerHTML = "";
  await loadConversations();
}

async function selectConversation(id) {
  currentConversationId = id;
  messagesEl.innerHTML = "";
  clearError();
  const data = await api("/api/conversations/" + id);
  for (const msg of data.messages) {
    addMessage(msg.role, msg.content, false);
  }
  await loadConversations();
}

// POST to a streaming (SSE) endpoint and stream the reply into bubble.
// Returns the full text, or throws on error.
async function streamInto(url, body, bubble) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || ("Request failed: " + res.status));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const event = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      for (const line of event.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") break;
        let obj;
        try {
          obj = JSON.parse(payload);
        } catch (e) {
          continue;
        }
        if (obj.delta) {
          fullText += obj.delta;
          bubble.innerHTML = renderRich(fullText);
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }
        if (obj.error) {
          throw new Error(obj.error);
        }
      }
    }
  }

  return fullText;
}

async function sendMessage(text) {
  clearError();
  addMessage("user", text, false);
  const bubble = addMessage("assistant", "", true);
  isStreaming = true;
  sendBtn.disabled = true;

  try {
    const fullText = await streamInto("/api/chat", {
      conversation_id: currentConversationId,
      message: text,
    }, bubble);
    bubble.classList.remove("streaming");
    if (!fullText) {
      bubble.remove();
    }
    // Titles are set from the first message, refresh the sidebar.
    await loadConversations();
  } catch (e) {
    bubble.remove();
    showError(e.message);
  } finally {
    isStreaming = false;
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

async function runSubagents(task) {
  clearError();
  addMessage("user", "/team " + task, false);
  const bubble = addMessage("assistant", "Consulting the team...", true);
  isStreaming = true;
  sendBtn.disabled = true;

  try {
    const data = await api("/api/subagents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task: task,
        conversation_id: currentConversationId,
      }),
    });

    const text = data.synthesis || "The team returned no merged answer.";
    bubble.innerHTML = renderRich(text);
    bubble.classList.remove("streaming");
    await loadConversations();
  } catch (e) {
    bubble.remove();
    showError(e.message);
  } finally {
    isStreaming = false;
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

async function runWebSearch(query) {
  clearError();
  addMessage("user", "/web " + query, false);
  const bubble = addMessage("assistant", "Searching the web...", true);
  isStreaming = true;
  sendBtn.disabled = true;

  try {
    const fullText = await streamInto("/api/web", {
      query: query,
      conversation_id: currentConversationId,
    }, bubble);
    bubble.classList.remove("streaming");
    if (!fullText) {
      bubble.remove();
    }
    await loadConversations();
  } catch (e) {
    bubble.remove();
    showError(e.message);
  } finally {
    isStreaming = false;
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  if (isStreaming) return;
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  const lower = text.toLowerCase();
  if (lower.startsWith("/team ")) {
    const task = text.slice(6).trim();
    if (!task) {
      showError("Usage: /team <task>");
      return;
    }
    runSubagents(task);
  } else if (lower.startsWith("/web ")) {
    const query = text.slice(5).trim();
    if (!query) {
      showError("Usage: /web <question>");
      return;
    }
    runWebSearch(query);
  } else {
    sendMessage(text);
  }
});

newChatBtn.addEventListener("click", () => {
  if (isStreaming) return;
  clearError();
  createConversation();
});

(async function init() {
  await loadBranding();
  const conversations = await api("/api/conversations");
  if (conversations.length === 0) {
    await createConversation();
  } else {
    await selectConversation(conversations[0].id);
  }
})();
