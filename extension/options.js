const DEFAULT_SETTINGS = {
  serverUrl: "ws://127.0.0.1:8000/captcha_ws",
  apiKey: "",
  routeKey: "",
  clientLabel: "",
  maxConcurrency: 3
};
const RUNTIME_STATE_KEY = "runtimeState";
const RUNTIME_LOGS_KEY = "runtimeLogs";
const RUNTIME_STATE_STALE_MS = 90000;

const $ = (id) => document.getElementById(id);

function normalizeSettings(values) {
  const rawConcurrency = parseInt(values.maxConcurrency, 10);
  return {
    serverUrl: (values.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
    apiKey: (values.apiKey || "").trim(),
    routeKey: (values.routeKey || "").trim(),
    clientLabel: (values.clientLabel || "").trim(),
    maxConcurrency: (!isNaN(rawConcurrency) && rawConcurrency >= 1 && rawConcurrency <= 8)
      ? rawConcurrency
      : DEFAULT_SETTINGS.maxConcurrency
  };
}

function setStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.style.color = isError ? "#b91c1c" : "#065f46";
}

function isValidWsUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "ws:" || url.protocol === "wss:";
  } catch (e) {
    return false;
  }
}

function loadSettings() {
  chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
    const settings = normalizeSettings(stored);
    $("serverUrl").value = settings.serverUrl;
    $("apiKey").value = settings.apiKey;
    $("routeKey").value = settings.routeKey;
    $("clientLabel").value = settings.clientLabel;
    $("maxConcurrency").value = settings.maxConcurrency;
  });
}

function saveSettings() {
  const settings = normalizeSettings({
    serverUrl: $("serverUrl").value,
    apiKey: $("apiKey").value,
    routeKey: $("routeKey").value,
    clientLabel: $("clientLabel").value,
    maxConcurrency: $("maxConcurrency").value
  });

  if (!isValidWsUrl(settings.serverUrl)) {
    setStatus("WebSocket URL 必须以 ws:// 或 wss:// 开头。", true);
    return;
  }
  if (!settings.apiKey) {
    setStatus("请填写 Flow2API API Key。", true);
    return;
  }

  chrome.storage.local.set(settings, () => {
    if (chrome.runtime.lastError) {
      setStatus(`保存失败：${chrome.runtime.lastError.message}`, true);
      return;
    }
    setStatus("已保存，后台连接会自动重连。");
  });
}

const STATUS_LABELS = {
  connecting: "连接中",
  connected: "已连接",
  busy: "执行中",
  disconnected: "未连接",
  error: "连接/任务错误",
};

function formatTask(task) {
  if (!task) return "无";
  const action = task.action || "未知动作";
  const projectId = task.projectId ? `，project_id=${task.projectId}` : "";
  const active = task.activeCount ? ` (并发中: ${task.activeCount})` : "";
  return `${task.type || "任务"}，${action}${projectId}${active}`;
}

function renderRuntime(stored) {
  const state = stored.runtimeState && typeof stored.runtimeState === "object" ? stored.runtimeState : {};
  let status = state.status || "disconnected";
  const updatedAt = Number(state.updatedAt || 0);
  const stateAge = updatedAt > 0 ? Date.now() - updatedAt : Infinity;
  const stale = ["connected", "busy", "connecting"].includes(status) && stateAge > RUNTIME_STATE_STALE_MS;
  if (stale) status = "stale";
  const statusElement = $("runtimeStatus");
  statusElement.textContent = stale ? "状态已过期（请重新连接）" : (STATUS_LABELS[status] || status);
  statusElement.dataset.status = status;
  $("runtimeRouteKey").textContent = stored.routeKey || "(empty)";
  $("runtimeTask").textContent = formatTask(state.currentTask);
  let runtimeError = state.lastError || "无";
  if (stale) {
    const lastUpdated = updatedAt > 0
      ? new Date(updatedAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })
      : "未知";
    runtimeError = `最后状态更新时间：${lastUpdated}；服务端可能已重启或连接已断开`;
  }
  $("runtimeError").textContent = runtimeError;

  const lastConnected = Number(state.lastConnectedAt || 0);
  $("runtimeLastConnected").textContent = lastConnected
    ? new Date(lastConnected).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })
    : "无记录";

  const logs = Array.isArray(stored.runtimeLogs) ? stored.runtimeLogs : [];
  $("runtimeLogs").textContent = logs.length
    ? logs.map((item) => `${item.time || ""} [${item.level || "info"}] ${item.message || ""}`).join("\n")
    : "暂无日志";
  const logBox = $("runtimeLogs");
  logBox.scrollTop = logBox.scrollHeight;
}

function loadRuntime() {
  chrome.storage.local.get({
    ...DEFAULT_SETTINGS,
    [RUNTIME_STATE_KEY]: null,
    [RUNTIME_LOGS_KEY]: [],
  }, renderRuntime);
}

// Re-evaluate freshness even when the background worker is suspended and no
// storage event is emitted.  This prevents yesterday's persisted "connected"
// state from remaining visible indefinitely.
setInterval(loadRuntime, 15000);

function reconnect() {
  chrome.runtime.sendMessage({ type: "reconnect" }, (response) => {
    if (chrome.runtime.lastError) {
      setStatus(`重新连接失败：${chrome.runtime.lastError.message}`, true);
      return;
    }
    setStatus(response && response.ok ? "已请求后台重新连接。" : "后台未响应。", !(response && response.ok));
  });
}

function clearLogs() {
  chrome.storage.local.set({ [RUNTIME_LOGS_KEY]: [] }, () => {
    if (chrome.runtime.lastError) {
      setStatus(`清空日志失败：${chrome.runtime.lastError.message}`, true);
      return;
    }
    setStatus("日志已清空。");
    loadRuntime();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  loadRuntime();
  $("saveBtn").addEventListener("click", saveSettings);
  $("reconnectBtn").addEventListener("click", reconnect);
  $("clearLogsBtn").addEventListener("click", clearLogs);
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.runtimeState || changes.runtimeLogs || changes.routeKey || changes.clientLabel) {
      loadRuntime();
    }
  });
});
