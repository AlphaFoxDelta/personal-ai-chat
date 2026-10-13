// Personal AI chat frontend: sidebar conversations, streaming chat,
// plus the /team command which fans a task out to subagents.

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

function addMessage(role, text, streaming) {
  const div = document.createElement("div");
  div.className = "message " + role + (streaming ? " streaming" : "");
  div.textContent = text;
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

async function sendMessage(text) {
  clearError();
  addMessage("user", text, false);
  const bubble = addMessage("assistant", "", true);
  isStreaming = true;
  sendBtn.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: currentConversationId,
        message: text,
      }),
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
            bubble.textContent = fullText;
            messagesEl.scrollTop = messagesEl.scrollHeight;
          }
          if (obj.error) {
            throw new Error(obj.error);
          }
        }
      }
    }

    bubble.classList.remove("streaming");
    if (!fullText) {
      bubble.remove();
    }
    // Titles are set from the first message, refresh the sidebar.
    await loadConversations();
  } catch (e) {
    bubble.classList.remove("streaming");
    bubble.textContent = "";
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

    let text = data.synthesis || "The team returned no merged answer.";
    const notes = (data.results || []).map(function (r) {
      return "- " + r.name + ": " + (r.output || r.error || "(no output)");
    });
    if (notes.length > 0) {
      text += "\n\n---\nTeam notes:\n" + notes.join("\n");
    }
    bubble.textContent = text;
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

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  if (isStreaming) return;
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  if (text.toLowerCase().startsWith("/team ")) {
    const task = text.slice(6).trim();
    if (!task) {
      showError("Usage: /team <task>");
      return;
    }
    runSubagents(task);
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
