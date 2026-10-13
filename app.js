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

const rawTextByEl = new WeakMap();
const REACT_EMOJIS = ["\uD83D\uDC4D", "\u2764\uFE0F", "\uD83D\uDE02",
                      "\uD83D\uDE2E", "\uD83D\uDE22", "\uD83C\uDF89"];

// Avatar picker: 4 fox avatars, choice stored in localStorage.
const AVATARS = [
  { id: "fox-ember", name: "Ember" },
  { id: "fox-shadow", name: "Shadow" },
  { id: "fox-neon", name: "Neon" },
  { id: "fox-ink", name: "Ink" },
];
const AVATAR_KEY = "kitsune_avatar";

function getAvatarId() {
  const id = localStorage.getItem(AVATAR_KEY);
  return AVATARS.some(function (a) { return a.id === id; }) ? id : "fox-ember";
}

let avatarWorking = false;

function avatarUrl(id) {
  const key = avatarWorking ? id + "-working" : id;
  return AVATAR_DATA[key] || AVATAR_DATA[id] || AVATAR_DATA["fox-ember"];
}

// Central busy-state switch: flips isStreaming and swaps every visible
// avatar to its working variant (and back when done).
function setBusy(busy) {
  isStreaming = busy;
  if (avatarWorking !== busy) {
    avatarWorking = busy;
    applyAvatar(getAvatarId());
  }
}

function avatarName(id) {
  const a = AVATARS.find(function (x) { return x.id === id; });
  return a ? a.name : "Ember";
}

function applyAvatar(id) {
  const url = avatarUrl(id);
  document.getElementById("profile-avatar").src = url;
  document.getElementById("avatar-current-name").textContent = avatarName(id);
  document.querySelectorAll(".msg-avatar").forEach(function (img) {
    img.src = url;
  });
  document.querySelectorAll(".profile-avatar-option").forEach(function (btn) {
    btn.classList.toggle("selected", btn.dataset.avatar === id);
  });
}

const AVATAR_GRID_KEY = "kitsune_avatar_grid";

function setAvatarGridOpen(open) {
  document.getElementById("profile-avatar-grid").classList.toggle("hidden", !open);
  document.getElementById("avatar-chevron").classList.toggle("collapsed", !open);
  localStorage.setItem(AVATAR_GRID_KEY, open ? "open" : "closed");
}

function setAvatar(id) {
  localStorage.setItem(AVATAR_KEY, id);
  applyAvatar(id);
  setAvatarGridOpen(false); // The selection goes away once picked.
}

function buildProfileGrid() {
  const grid = document.getElementById("profile-avatar-grid");
  grid.innerHTML = "";
  AVATARS.forEach(function (a) {
    const btn = document.createElement("button");
    btn.className = "profile-avatar-option";
    btn.dataset.avatar = a.id;
    btn.innerHTML = '<img src="' + avatarUrl(a.id) + '" alt="' + a.name + '">' +
      "<span>" + a.name + "</span>";
    btn.addEventListener("click", function () {
      setAvatar(a.id);
    });
    grid.appendChild(btn);
  });
}

const PANEL_KEY = "kitsune_panel";

function setPanelOpen(open) {
  document.getElementById("profile-panel").classList.toggle("hidden", !open);
  localStorage.setItem(PANEL_KEY, open ? "open" : "closed");
}

// Activity feed: what you've done in the app, grouped by day.
const ACTIVITY_ICONS = { chat: "\uD83D\uDCAC", web: "\uD83D\uDD0D", team: "\uD83D\uDC65" };
const ACTIVITY_KINDS = { chat: "Chat", web: "Web search", team: "Team run" };

function activityDayLabel(iso) {
  const d = new Date(iso);
  const now = new Date();
  const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const days = Math.round((startOf(now) - startOf(d)) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function activityTime(iso) {
  return new Date(iso).toLocaleTimeString(undefined,
    { hour: "numeric", minute: "2-digit" });
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderActivity(items) {
  const feed = document.getElementById("activity-feed");
  if (!items.length) {
    feed.innerHTML = '<div class="activity-empty">Nothing yet.<br>Start chatting.</div>';
    return;
  }
  let html = "";
  let lastDay = null;
  items.forEach(function (it) {
    const day = activityDayLabel(it.created_at);
    if (day !== lastDay) {
      html += '<div class="activity-day">' + day + "</div>";
      lastDay = day;
    }
    const icon = ACTIVITY_ICONS[it.kind] || "\u2728";
    const kind = ACTIVITY_KINDS[it.kind] || it.kind;
    html += '<div class="activity-item">' +
      '<div class="activity-icon">' + icon + "</div>" +
      '<div class="activity-text">' +
      '<div class="activity-label">' + escapeHtml(it.label) + "</div>" +
      '<div class="activity-detail">' + escapeHtml(kind) +
      " · " + activityTime(it.created_at) + "</div>" +
      "</div></div>";
  });
  feed.innerHTML = html;
}

async function loadActivity() {
  try {
    renderActivity(await api("/api/activity"));
  } catch (e) {
    // Activity is a nice-to-have; never break the page over it.
  }
}

function setReactionBadge(div, emoji) {
  let badge = div.querySelector(":scope > .reaction-badge");
  if (!emoji) {
    if (badge) badge.remove();
    return;
  }
  if (!badge) {
    badge = document.createElement("div");
    badge.className = "reaction-badge";
    div.appendChild(badge);
  }
  badge.textContent = emoji;
}

function addMessage(role, text, streaming, opts) {
  opts = opts || {};
  const div = document.createElement("div");
  div.className = "message " + role + (streaming ? " streaming" : "");
  if (opts.id) div.dataset.messageId = opts.id;
  const pickerBtns = REACT_EMOJIS.map(function (e) {
    return '<button class="emoji-option" data-emoji="' + e + '">' + e + "</button>";
  }).join("");
  div.innerHTML =
    (role === "assistant"
      ? '<img class="msg-avatar" src="' + avatarUrl(getAvatarId()) + '" alt="">'
      : "") +
    '<div class="message-content">' + renderRich(text) + "</div>" +
    '<div class="message-actions">' +
    '<button class="action-btn" data-action="react" title="React">\uD83D\uDE0A</button>' +
    '<button class="action-btn" data-action="reply" title="Reply">\u21A9\uFE0F</button>' +
    '<button class="action-btn" data-action="copy" title="Copy">\uD83D\uDCCB</button>' +
    "</div>" +
    '<div class="emoji-picker hidden">' + pickerBtns + "</div>";
  rawTextByEl.set(div, text);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  setReactionBadge(div, opts.reaction);
  return div;
}

async function loadBranding() {
  try {
    const cfg = await api("/api/config");
    document.getElementById("app-name").textContent = cfg.app_name;
    document.getElementById("page-title").textContent = cfg.app_name;
    document.getElementById("profile-name").textContent = cfg.app_name;
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
    addMessage(msg.role, msg.content, false,
               { id: msg.id, reaction: msg.reactions });
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
  let doneInfo = null;

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
          bubble.querySelector(".message-content").innerHTML = renderRich(fullText);
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }
        if (obj.done) {
          doneInfo = obj;
        }
        if (obj.error) {
          throw new Error(obj.error);
        }
      }
    }
  }

  return { text: fullText, done: doneInfo };
}

async function sendMessage(text) {
  clearError();
  const userBubble = addMessage("user", text, false);
  const bubble = addMessage("assistant", "", true);
  setBusy(true);
  sendBtn.disabled = true;

  try {
    const result = await streamInto("/api/chat", {
      conversation_id: currentConversationId,
      message: text,
    }, bubble);
    const fullText = result.text;
    bubble.classList.remove("streaming");
    if (!fullText) {
      bubble.remove();
    } else {
      rawTextByEl.set(bubble, fullText);
      if (result.done) {
        if (result.done.user_message_id) {
          userBubble.dataset.messageId = result.done.user_message_id;
        }
        if (result.done.assistant_message_id) {
          bubble.dataset.messageId = result.done.assistant_message_id;
        }
      }
    }
    // Titles are set from the first message, refresh the sidebar.
    await loadConversations();
    loadActivity();
  } catch (e) {
    bubble.remove();
    showError(e.message);
  } finally {
    setBusy(false);
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

async function runSubagents(task) {
  clearError();
  const userBubble = addMessage("user", "/team " + task, false);
  const bubble = addMessage("assistant", "Consulting the team...", true);
  setBusy(true);
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
    bubble.querySelector(".message-content").innerHTML = renderRich(text);
    rawTextByEl.set(bubble, text);
    if (data.user_message_id) {
      userBubble.dataset.messageId = data.user_message_id;
    }
    if (data.assistant_message_id) {
      bubble.dataset.messageId = data.assistant_message_id;
    }
    bubble.classList.remove("streaming");
    await loadConversations();
    loadActivity();
  } catch (e) {
    bubble.remove();
    showError(e.message);
  } finally {
    setBusy(false);
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

async function runWebSearch(query) {
  clearError();
  const userBubble = addMessage("user", "/web " + query, false);
  const bubble = addMessage("assistant", "Searching the web...", true);
  setBusy(true);
  sendBtn.disabled = true;

  try {
    const result = await streamInto("/api/web", {
      query: query,
      conversation_id: currentConversationId,
    }, bubble);
    const fullText = result.text;
    bubble.classList.remove("streaming");
    if (!fullText) {
      bubble.remove();
    } else {
      rawTextByEl.set(bubble, fullText);
      if (result.done) {
        if (result.done.user_message_id) {
          userBubble.dataset.messageId = result.done.user_message_id;
        }
        if (result.done.assistant_message_id) {
          bubble.dataset.messageId = result.done.assistant_message_id;
        }
      }
    }
    await loadConversations();
    loadActivity();
  } catch (e) {
    bubble.remove();
    showError(e.message);
  } finally {
    setBusy(false);
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

// Message actions: react, reply, copy. One delegated listener.
messagesEl.addEventListener("click", async (e) => {
  const msgEl = e.target.closest(".message");
  const btn = e.target.closest("[data-action], .emoji-option");
  if (btn && msgEl) {
    e.stopPropagation();
    const raw = rawTextByEl.get(msgEl) || "";
    if (btn.classList.contains("emoji-option")) {
      msgEl.querySelector(".emoji-picker").classList.add("hidden");
      const id = msgEl.dataset.messageId;
      if (!id) return;
      try {
        const data = await api("/api/messages/" + id + "/react", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ emoji: btn.dataset.emoji }),
        });
        setReactionBadge(msgEl, data.reaction);
      } catch (err) {
        showError("Couldn't save the reaction.");
      }
      return;
    }
    const action = btn.dataset.action;
    if (action === "copy") {
      try {
        await navigator.clipboard.writeText(raw);
        const orig = btn.textContent;
        btn.textContent = "\u2713";
        setTimeout(() => { btn.textContent = orig; }, 1000);
      } catch (err) {
        showError("Copy failed.");
      }
    } else if (action === "reply") {
      const lines = raw.split("\n").slice(0, 4).join("\n");
      const short = lines.length > 200 ? lines.slice(0, 200) + "..." : lines;
      const quoted = short.split("\n").map((l) => "> " + l).join("\n");
      chatInput.value = quoted + "\n\n" + chatInput.value;
      chatInput.focus();
    } else if (action === "react") {
      msgEl.querySelector(".emoji-picker").classList.toggle("hidden");
    }
    return;
  }
  // Touch devices have no hover: tapping a message toggles its actions.
  // Link taps are left alone.
  if (msgEl && !e.target.closest("a") &&
      window.matchMedia("(hover: none)").matches) {
    msgEl.classList.toggle("show-actions");
  }
});

// Tapping anywhere else closes open emoji pickers.
document.addEventListener("click", (e) => {
  if (!e.target.closest(".emoji-picker") &&
      !e.target.closest('[data-action="react"]')) {
    document.querySelectorAll(".emoji-picker").forEach((p) => {
      p.classList.add("hidden");
    });
  }
});

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
  buildProfileGrid();
  applyAvatar(getAvatarId());
  const panelStored = localStorage.getItem(PANEL_KEY);
  setPanelOpen(panelStored ? panelStored === "open" : window.innerWidth > 1100);
  document.getElementById("panel-toggle").addEventListener("click", function () {
    const panel = document.getElementById("profile-panel");
    setPanelOpen(panel.classList.contains("hidden"));
  });
  const gridStored = localStorage.getItem(AVATAR_GRID_KEY);
  setAvatarGridOpen(gridStored ? gridStored === "open" : true);
  document.getElementById("avatar-section-toggle").addEventListener("click", function () {
    const grid = document.getElementById("profile-avatar-grid");
    setAvatarGridOpen(grid.classList.contains("hidden"));
  });
  loadActivity();
  const conversations = await api("/api/conversations");
  if (conversations.length === 0) {
    await createConversation();
  } else {
    await selectConversation(conversations[0].id);
  }
})();
