"use strict";

const STATE_LABEL = {
  idle: "空闲",
  preparing: "准备中",
  recording: "录音中",
  finalizing: "识别中",
};

const liveText = document.getElementById("live-text");
const liveMessage = document.getElementById("live-message");
const stateDot = document.getElementById("state-dot");
const stateLabel = document.getElementById("state-label");
const toggleBtn = document.getElementById("toggle-btn");
const refreshBtn = document.getElementById("refresh-btn");
const inputDeviceSelect = document.getElementById("input-device");
const reloadDevicesBtn = document.getElementById("reload-devices-btn");
const historyContextToggle = document.getElementById("history-context-toggle");
const historyList = document.getElementById("history-list");

let ws = null;
let currentState = "idle";

function setState(state) {
  currentState = state;
  stateDot.className = "dot " + state;
  stateLabel.textContent = STATE_LABEL[state] || state;
  toggleBtn.textContent =
    state === "recording"
      ? "结束录音"
      : state === "preparing"
        ? "准备中"
        : "开始录音";
  toggleBtn.classList.toggle("recording", state === "recording");
  toggleBtn.disabled = state === "preparing" || state === "finalizing";
  inputDeviceSelect.disabled = state !== "idle";
  reloadDevicesBtn.disabled = state !== "idle";
  if (state === "idle") {
    setLiveText("…等待语音输入", true);
  } else if (state === "preparing") {
    setLiveText("正在准备麦克风和识别连接，请稍候", true);
  } else if (state === "recording") {
    setLiveText("可以开始说话", true);
  } else if (state === "finalizing") {
    setLiveText("正在识别，请稍候", true);
  }
}

function setLiveText(text, placeholder = false) {
  liveMessage.textContent = text;
  liveMessage.classList.toggle("placeholder", placeholder);
  liveText.classList.toggle("placeholder", placeholder);
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    refreshHistory();
    loadInputDevices();
    loadContextSettings();
  };
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.kind === "state") {
      setState(data.payload.state);
    } else if (data.kind === "partial") {
      setLiveText(data.payload.text);
    } else if (data.kind === "final") {
      setLiveText(data.payload.text);
      // 一会儿后刷新历史（后端落库需要短暂时间）
      setTimeout(refreshHistory, 600);
    } else if (data.kind === "error") {
      setLiveText("⚠ " + data.payload.message, true);
    }
  };
  ws.onclose = () => {
    setTimeout(connectWS, 1000);
  };
}

function sendToggle() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: "toggle" }));
  }
}

toggleBtn.addEventListener("click", sendToggle);
refreshBtn.addEventListener("click", refreshHistory);
reloadDevicesBtn.addEventListener("click", loadInputDevices);
inputDeviceSelect.addEventListener("change", selectInputDevice);
historyContextToggle.addEventListener("change", updateContextSettings);

// ── 输入设备 ─────────────────────────────────────────────────────

async function loadInputDevices() {
  try {
    const resp = await fetch("/api/audio/input-devices");
    const data = await resp.json();
    renderInputDevices(data);
  } catch (e) {
    console.error("input devices load failed", e);
  }
}

function renderInputDevices(data) {
  const selected = data.selected == null ? "" : String(data.selected);
  inputDeviceSelect.innerHTML = "";

  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "系统默认输入";
  inputDeviceSelect.appendChild(defaultOption);

  for (const device of data.devices || []) {
    const option = document.createElement("option");
    option.value = String(device.id);
    option.textContent = `${device.name} (${device.channels}ch)${
      device.default ? " - 默认" : ""
    }`;
    inputDeviceSelect.appendChild(option);
  }

  inputDeviceSelect.value = selected;
}

async function selectInputDevice() {
  const raw = inputDeviceSelect.value;
  const deviceId = raw === "" ? null : Number(raw);
  try {
    const resp = await fetch("/api/audio/input-device", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: deviceId }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setLiveText(data.error || "输入设备切换失败", true);
      await loadInputDevices();
      return;
    }
    renderInputDevices(data);
  } catch (e) {
    console.error("input device select failed", e);
    setLiveText("输入设备切换失败", true);
    await loadInputDevices();
  }
}

// ── 上下文设置 ───────────────────────────────────────────────────

async function loadContextSettings() {
  try {
    const resp = await fetch("/api/context/settings");
    const data = await resp.json();
    renderContextSettings(data);
  } catch (e) {
    console.error("context settings load failed", e);
  }
}

function renderContextSettings(data) {
  const settings = data.history_context || {};
  historyContextToggle.disabled = settings.available === false;
  historyContextToggle.checked = settings.enabled !== false;
}

async function updateContextSettings() {
  historyContextToggle.disabled = true;
  try {
    const resp = await fetch("/api/context/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        history_context_enabled: historyContextToggle.checked,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setLiveText(data.error || "上下文设置更新失败", true);
      await loadContextSettings();
      return;
    }
    renderContextSettings(data);
  } catch (e) {
    console.error("context settings update failed", e);
    setLiveText("上下文设置更新失败", true);
    await loadContextSettings();
  } finally {
    historyContextToggle.disabled = false;
  }
}

// ── 历史 ─────────────────────────────────────────────────────────

async function refreshHistory() {
  try {
    const resp = await fetch("/api/history");
    const items = await resp.json();
    renderHistory(items);
  } catch (e) {
    console.error("history load failed", e);
  }
}

function renderHistory(items) {
  historyList.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "（尚无历史，按 F2 试试）";
    historyList.appendChild(li);
    return;
  }
  for (const item of items) {
    historyList.appendChild(buildHistoryItem(item));
  }
}

function buildHistoryItem(item) {
  const li = document.createElement("li");
  li.className = "history-item";
  li.dataset.id = item.id;

  const meta = document.createElement("div");
  meta.className = "history-meta";

  const time = document.createElement("span");
  time.textContent = new Date(item.created_at * 1000).toLocaleString("zh-CN");
  meta.appendChild(time);

  const actions = document.createElement("div");
  actions.className = "history-actions";

  const copyBtn = makeButton("复制", async () => {
    const text = textEl.textContent;
    await fetch("/api/copy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    copyBtn.textContent = "已复制";
    copyBtn.classList.add("copied");
    setTimeout(() => {
      copyBtn.textContent = "复制";
      copyBtn.classList.remove("copied");
    }, 1200);
  });

  const editBtn = makeButton("编辑", () => {
    if (textEl.contentEditable === "true") {
      saveEdit();
    } else {
      enterEdit();
    }
  });

  const delBtn = makeButton("删除", async () => {
    if (!confirm("删除这条记录？")) return;
    await fetch(`/api/history/${item.id}`, { method: "DELETE" });
    li.remove();
  });

  actions.append(copyBtn, editBtn, delBtn);
  meta.appendChild(actions);

  const textEl = document.createElement("div");
  textEl.className = "history-text";
  textEl.textContent = item.text;
  textEl.spellcheck = false;

  li.append(meta, textEl);

  function enterEdit() {
    textEl.contentEditable = "true";
    textEl.classList.add("editing");
    textEl.focus();
    editBtn.textContent = "保存";
  }

  async function saveEdit() {
    textEl.contentEditable = "false";
    textEl.classList.remove("editing");
    editBtn.textContent = "编辑";
    const newText = textEl.textContent;
    if (newText === item.text) return;
    try {
      await fetch(`/api/history/${item.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: newText }),
      });
      item.text = newText;
    } catch (e) {
      console.error("save failed", e);
    }
  }

  textEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      saveEdit();
    } else if (e.key === "Escape") {
      textEl.textContent = item.text;
      saveEdit();
    }
  });

  return li;
}

function makeButton(label, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

setState("idle");
connectWS();
