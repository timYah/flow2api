let ws = null;
let reconnectTimeout = null;
let reconnectAttempt = 0;
let connectionWatchdog = null;
let heartbeatInterval = null;
const MAX_RUNTIME_LOGS = 100;
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 32000];
const CONNECTION_ATTEMPT_TIMEOUT_MS = 10000;
const RUNTIME_STATE_KEY = "runtimeState";
const RUNTIME_LOGS_KEY = "runtimeLogs";
let runtimeState = {
    status: "disconnected",
    currentTask: null,
    lastError: "",
    lastConnectedAt: null,
    updatedAt: Date.now(),
};
let runtimeLogs = [];
let runtimeStateReady = new Promise((resolve) => {
    chrome.storage.local.get({
        [RUNTIME_STATE_KEY]: null,
        [RUNTIME_LOGS_KEY]: [],
    }, (stored) => {
        if (stored[RUNTIME_STATE_KEY] && typeof stored[RUNTIME_STATE_KEY] === "object") {
            runtimeState = { ...runtimeState, ...stored[RUNTIME_STATE_KEY] };
        }
        if (Array.isArray(stored[RUNTIME_LOGS_KEY])) {
            runtimeLogs = stored[RUNTIME_LOGS_KEY].slice(-MAX_RUNTIME_LOGS);
        }
        resolve();
    });
});

// batch_id -> { tabId, cleanupTimer }
// 页内 fetch 到点没返回时保留其标签页，让带同一 batch_id 的重试回到同一个页面
// 认领结果，而不是重新发一次生成请求。
const retainedBatchTabs = new Map();
const RETAINED_TAB_TTL_MS = 10 * 60 * 1000;

async function closeRetainedTab(batchId) {
    const entry = retainedBatchTabs.get(batchId);
    if (!entry) return;
    retainedBatchTabs.delete(batchId);
    if (entry.cleanupTimer) clearTimeout(entry.cleanupTimer);
    try {
        await chrome.tabs.remove(entry.tabId);
    } catch (e) {
        console.log("[Flow2API] Retained tab already gone:", e);
    }
}

function scheduleRetainedTabCleanup(batchId, tabId) {
    const existing = retainedBatchTabs.get(batchId);
    if (existing && existing.cleanupTimer) clearTimeout(existing.cleanupTimer);
    const cleanupTimer = setTimeout(() => {
        closeRetainedTab(batchId).catch(() => {});
    }, RETAINED_TAB_TTL_MS);
    retainedBatchTabs.set(batchId, { tabId, cleanupTimer });
}

async function takeRetainedTab(batchId) {
    const entry = retainedBatchTabs.get(batchId);
    if (!entry) return null;
    if (entry.cleanupTimer) clearTimeout(entry.cleanupTimer);
    retainedBatchTabs.delete(batchId);
    try {
        const tab = await getTab(entry.tabId);
        if (tab && tab.id) return tab.id;
    } catch (e) {
        console.log("[Flow2API] Retained tab unavailable:", e);
    }
    return null;
}

chrome.tabs.onRemoved.addListener((tabId) => {
    for (const [batchId, entry] of retainedBatchTabs) {
        if (entry.tabId !== tabId) continue;
        if (entry.cleanupTimer) clearTimeout(entry.cleanupTimer);
        retainedBatchTabs.delete(batchId);
    }
});

const DEFAULT_SETTINGS = {
    serverUrl: "ws://127.0.0.1:8000/captcha_ws",
    apiKey: "",
    routeKey: "",
    clientLabel: "",
    maxConcurrency: 3
};

function getSettings() {
    return new Promise((resolve) => {
        chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
            const rawConcurrency = parseInt(stored.maxConcurrency, 10);
            resolve({
                serverUrl: (stored.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
                apiKey: (stored.apiKey || "").trim(),
                routeKey: (stored.routeKey || "").trim(),
                clientLabel: (stored.clientLabel || "").trim(),
                maxConcurrency: (!isNaN(rawConcurrency) && rawConcurrency >= 1 && rawConcurrency <= 8)
                    ? rawConcurrency
                    : DEFAULT_SETTINGS.maxConcurrency
            });
        });
    });
}

function closeSocket() {
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = null;
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
    if (connectionWatchdog) clearTimeout(connectionWatchdog);
    connectionWatchdog = null;
    reconnectAttempt = 0;
    if (ws) {
        try {
            ws.onopen = null;
            ws.onclose = null;
            ws.onerror = null;
            ws.onmessage = null;
            ws.close();
        } catch (e) {
            console.log("[Flow2API] Close socket error", e);
        }
        ws = null;
    }
}

function scheduleReconnect(reason = "WebSocket 已断开") {
    if (reconnectTimeout) return;

    const retryIndex = Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1);
    const delayMs = RECONNECT_DELAYS_MS[retryIndex];
    const retryNumber = reconnectAttempt + 1;
    reconnectAttempt = Math.min(reconnectAttempt + 1, RECONNECT_DELAYS_MS.length - 1);
    const delaySeconds = Math.round(delayMs / 1000);

    void appendRuntimeLog(
        "warn",
        `${reason}，${delaySeconds} 秒后进行第 ${retryNumber} 次重连`,
    );
    reconnectTimeout = setTimeout(() => {
        reconnectTimeout = null;
        connectWS().catch(async (error) => {
            const safeError = redactRuntimeText(error.message || error);
            await updateRuntimeState({
                status: "disconnected",
                currentTask: null,
                lastError: safeError,
            });
            await appendRuntimeLog("error", `重连初始化失败：${safeError}`);
            scheduleReconnect("WebSocket 重连初始化失败");
        });
    }, delayMs);
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function redactRuntimeText(value) {
    return String(value || "")
        .replace(/Bearer\s+[^\s"']+/gi, "Bearer [redacted]")
        .replace(/(token|access_token|session-token|cookie)\s*[:=]\s*[^,\s}]+/gi, "$1=[redacted]")
        .slice(0, 500);
}

async function persistRuntimeState() {
    await runtimeStateReady;
    runtimeState.updatedAt = Date.now();
    await new Promise((resolve) => {
        chrome.storage.local.set({
            [RUNTIME_STATE_KEY]: runtimeState,
            [RUNTIME_LOGS_KEY]: runtimeLogs.slice(-MAX_RUNTIME_LOGS),
        }, () => resolve());
    });
}

async function updateRuntimeState(patch) {
    await runtimeStateReady;
    runtimeState = { ...runtimeState, ...patch, updatedAt: Date.now() };
    await persistRuntimeState();
}

async function appendRuntimeLog(level, message) {
    await runtimeStateReady;
    runtimeLogs.push({
        time: new Date().toISOString(),
        level: level || "info",
        message: redactRuntimeText(message),
    });
    if (runtimeLogs.length > MAX_RUNTIME_LOGS) {
        runtimeLogs = runtimeLogs.slice(-MAX_RUNTIME_LOGS);
    }
    await persistRuntimeState();
}

let activeTasksCount = 0;
const taskQueue = [];
const activeTasks = new Map(); // req_id -> taskInfo
let keepAliveTimer = null;

function ensureKeepAlive() {
    if (keepAliveTimer) return;
    keepAliveTimer = setInterval(() => {
        if (activeTasks.size > 0) {
            try {
                chrome.runtime.getPlatformInfo(() => {});
            } catch (e) {}
        } else {
            clearInterval(keepAliveTimer);
            keepAliveTimer = null;
        }
    }, 15000);
}

async function acquireTaskSlot() {
    const settings = await getSettings();
    const maxConcurrency = Math.max(1, Math.min(8, Number(settings.maxConcurrency) || 3));
    if (activeTasksCount < maxConcurrency) {
        activeTasksCount++;
        return;
    }
    await new Promise((resolve) => taskQueue.push(resolve));
    activeTasksCount++;
}

function releaseTaskSlot() {
    activeTasksCount = Math.max(0, activeTasksCount - 1);
    if (taskQueue.length > 0) {
        const next = taskQueue.shift();
        if (next) next();
    }
}

async function recordTaskStart(reqId, taskInfo) {
    activeTasks.set(reqId, taskInfo);
    ensureKeepAlive();
    await refreshTaskRuntimeState();
}

async function recordTaskEnd(reqId, lastError = "") {
    activeTasks.delete(reqId);
    await refreshTaskRuntimeState(lastError);
}

async function refreshTaskRuntimeState(lastError = "") {
    const isConnected = ws && ws.readyState === WebSocket.OPEN;
    const count = activeTasks.size;
    const status = isConnected ? (count > 0 ? "busy" : "connected") : "disconnected";
    const latestTask = count > 0 ? Array.from(activeTasks.values())[count - 1] : null;
    const patch = {
        status,
        currentTask: latestTask ? {
            ...latestTask,
            activeCount: count,
        } : null,
    };
    if (lastError) {
        patch.lastError = lastError;
    }
    await updateRuntimeState(patch);
}

function getActiveSocket() {
    if (ws && ws.readyState === WebSocket.OPEN) return ws;
    return null;
}

function sendSocketMessage(socket, payload) {
    const target = (socket && socket.readyState === WebSocket.OPEN) ? socket : getActiveSocket();
    if (!target) {
        console.error("[Flow2API] Failed to send WebSocket message: no open socket", payload && payload.req_id);
        return false;
    }
    try {
        target.send(JSON.stringify(payload));
        return true;
    } catch (e) {
        console.error("[Flow2API] Failed to send WebSocket response", e);
        return false;
    }
}

function waitForTabReady(tabId, timeoutMs = 12000) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timer);
            resolve();
        };
        const onUpdated = (updatedTabId, changeInfo) => {
            if (updatedTabId === tabId && changeInfo.status === "complete") {
                finish();
            }
        };
        const timer = setTimeout(finish, timeoutMs);

        chrome.tabs.onUpdated.addListener(onUpdated);
        chrome.tabs.get(tabId, (tab) => {
            if (chrome.runtime.lastError) {
                finish();
                return;
            }
            if (tab && tab.status === "complete") {
                finish();
            }
        });
    });
}

async function registerProtectionScript() {
    try {
        if (!chrome.scripting || typeof chrome.scripting.registerContentScripts !== "function") return;
        const SCRIPT_ID = "flow2api_protection";
        const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [SCRIPT_ID] }).catch(() => []);
        if (existing && existing.length > 0) {
            await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] }).catch(() => {});
        }
        await chrome.scripting.registerContentScripts([{
            id: SCRIPT_ID,
            matches: ["https://flow.google.com/*", "https://labs.google/*"],
            js: ["inject_protection.js"],
            runAt: "document_start",
            world: "MAIN",
            persistAcrossSessions: false,
        }]);
        console.log("[Flow2API] Dynamic protection script registered");
    } catch (e) {
        console.debug("[Flow2API] Dynamic content script registration error:", e);
    }
}

async function getTab(tabId) {
    return new Promise((resolve, reject) => {
        chrome.tabs.get(tabId, (tab) => {
            if (chrome.runtime.lastError) {
                reject(new Error(chrome.runtime.lastError.message));
                return;
            }
            resolve(tab);
        });
    });
}

async function findExistingFlowTab(projectId = "") {
    try {
        const queryPatterns = [
            "https://flow.google.com/*",
            "https://labs.google/*"
        ];
        const tabs = await chrome.tabs.query({ url: queryPatterns });
        if (!tabs || tabs.length === 0) return null;

        if (projectId) {
            const exactProjectTab = tabs.find(t => t.url && t.url.includes(`/project/${projectId}`) && t.status === "complete");
            if (exactProjectTab) return exactProjectTab;
            const anyProjectTab = tabs.find(t => t.url && t.url.includes(`/project/${projectId}`));
            if (anyProjectTab) return anyProjectTab;
        }

        const completedFlowTab = tabs.find(t => t.url && t.url.startsWith("https://flow.google.com/") && t.status === "complete");
        if (completedFlowTab) return completedFlowTab;

        const anyCompletedTab = tabs.find(t => t.status === "complete");
        return anyCompletedTab || tabs[0];
    } catch (e) {
        return null;
    }
}

async function waitForFlowPage(tabId, projectId = "", timeoutMs = 30000) {
    const deadline = Date.now() + timeoutMs;
    let lastUrl = "";
    while (Date.now() < deadline) {
        try {
            const tab = await getTab(tabId);
            lastUrl = (tab && tab.url) || "";
            if (!lastUrl || lastUrl === "about:blank") {
                await sleep(500);
                continue;
            }
            let url;
            try {
                url = new URL(lastUrl);
            } catch (err) {
                await sleep(500);
                continue;
            }
            const isLegacyFlowPath = url.hostname === "labs.google"
                && url.pathname.startsWith("/fx/tools/flow");
            const isCurrentFlowPath = url.hostname === "flow.google.com"
                && (url.pathname === "/" || url.pathname.startsWith("/project/"));
            if (!isLegacyFlowPath && !isCurrentFlowPath) {
                await sleep(500);
                continue;
            }
            if (projectId) {
                const actualPath = url.pathname.replace(/\/+$/, "");
                const expectedPaths = [
                    `/fx/tools/flow/project/${encodeURIComponent(projectId)}`,
                    `/project/${encodeURIComponent(projectId)}`,
                ];
                if (!expectedPaths.includes(actualPath)) {
                    await sleep(500);
                    continue;
                }
            }
            if (tab.status !== "complete") {
                await sleep(500);
                continue;
            }
            return tab;
        } catch (e) {
            await sleep(500);
        }
        await sleep(500);
    }
    throw new Error(`Flow 页面未就绪，当前页面为 ${lastUrl || "未知"}。请确认 Chrome 已登录 Google Labs。`);
}

function getCookiesForUrl(url) {
    return new Promise((resolve) => {
        chrome.cookies.getAll({ url }, (cookies) => {
            if (chrome.runtime.lastError || !Array.isArray(cookies)) {
                resolve([]);
                return;
            }
            resolve(cookies);
        });
    });
}

async function collectRuntimeCookies() {
    const cookieUrls = ["https://labs.google/", "https://flow.google.com/", "https://www.google.com/", "https://www.recaptcha.net/"];
    const allCookies = (await Promise.all(cookieUrls.map(getCookiesForUrl))).flat();
    const allowedDomains = ["labs.google", "flow.google.com", "google.com", "recaptcha.net"];
    const cookieMap = new Map();
    for (const cookie of allCookies) {
        const domain = String(cookie.domain || "").replace(/^\./, "").toLowerCase();
        if (!allowedDomains.some((allowed) => domain === allowed || domain.endsWith(`.${allowed}`))) continue;
        if (!cookie.name || cookie.value === undefined) continue;
        cookieMap.set(cookie.name, cookie.value);
    }
    return Object.fromEntries(cookieMap.entries());
}

async function getAllNextAuthSessionCookies() {
    const found = [];
    const names = ["__Secure-next-auth.session-token", "next-auth.session-token"];
    
    // 1. 查找 domain: "labs.google" 下所有 Cookie (包含所有子路径如 /fx 等)
    try {
        const labsCookies = await chrome.cookies.getAll({ domain: "labs.google" });
        for (const c of labsCookies) {
            if (names.includes(c.name) && c.value && c.value.trim()) {
                found.push(c.value.trim());
            }
        }
    } catch (e) {}

    // 2. 查找具体应用 URL 下的 Cookie
    try {
        const urlCookies = await chrome.cookies.getAll({ url: "https://labs.google/fx/vi/tools/flow" });
        for (const c of urlCookies) {
            if (names.includes(c.name) && c.value && c.value.trim()) {
                found.push(c.value.trim());
            }
        }
    } catch (e) {}

    try {
        const fxCookies = await chrome.cookies.getAll({ url: "https://labs.google/fx" });
        for (const c of fxCookies) {
            if (names.includes(c.name) && c.value && c.value.trim()) {
                found.push(c.value.trim());
            }
        }
    } catch (e) {}

    // 3. 查找 flow.google.com
    try {
        const flowCookies = await chrome.cookies.getAll({ domain: "flow.google.com" });
        for (const c of flowCookies) {
            if (names.includes(c.name) && c.value && c.value.trim()) {
                found.push(c.value.trim());
            }
        }
    } catch (e) {}

    return Array.from(new Set(found));
}

async function getNextAuthSessionCookie() {
    const all = await getAllNextAuthSessionCookies();
    return all.length > 0 ? all[0] : null;
}

async function triggerLabsNextAuthSignIn(email = "") {
    let csrfToken = "";
    try {
        const csrfResp = await fetch("https://labs.google/fx/api/auth/csrf", { credentials: "include" });
        if (csrfResp.ok) {
            const csrfData = await csrfResp.json();
            csrfToken = csrfData.csrfToken || "";
        }
    } catch (e) {
        console.warn("[Flow2API] Failed to get CSRF token via fetch:", e);
    }

    let oauthUrl = "";
    if (csrfToken) {
        try {
            const signinResp = await fetch("https://labs.google/fx/api/auth/signin/google", {
                method: "POST",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://labs.google",
                    "Referer": "https://labs.google/fx",
                },
                body: new URLSearchParams({
                    csrfToken: csrfToken,
                    callbackUrl: "https://labs.google/fx",
                    json: "true",
                }),
                credentials: "include",
            });
            if (signinResp.ok) {
                const signinData = await signinResp.json();
                oauthUrl = signinData.url || signinData.redirect || "";
            }
        } catch (e) {
            console.warn("[Flow2API] Failed to trigger NextAuth signin via fetch:", e);
        }
    }

    if (oauthUrl) {
        if (email) {
            try {
                const parsedUrl = new URL(oauthUrl);
                parsedUrl.searchParams.set("login_hint", email);
                oauthUrl = parsedUrl.toString();
            } catch (e) {
                // ignore URL parse error
            }
        }
        return oauthUrl;
    }

    return "https://labs.google/fx";
}

function summarizeInjectedResult(results) {
    if (!Array.isArray(results) || results.length === 0) {
        return "results=empty";
    }
    const injection = results[0];
    if (!injection || !Object.prototype.hasOwnProperty.call(injection, "result")) {
        return `results[0]=${injection ? "missing_result" : "empty"}`;
    }
    const result = injection.result;
    if (result === null) return "result_type=null";
    if (typeof result === "object") {
        const keys = Object.keys(result).slice(0, 12).join(",") || "(none)";
        const phase = result.diagnostics && result.diagnostics.phase
            ? `, phase=${result.diagnostics.phase}`
            : "";
        const flowStatus = result.flow_response && result.flow_response.status
            ? `, flow_http=${result.flow_response.status}`
            : "";
        return `result_type=object, keys=[${keys}]${phase}${flowStatus}`;
    }
    return `result_type=${typeof result}, length=${typeof result === "string" ? result.length : 0}`;
}

function normalizeInjectedResult(results, expectsFlowResponse) {
    if (!Array.isArray(results) || results.length === 0) {
        return {
            ok: false,
            message: "Injected script returned no result",
            summary: "results=empty",
        };
    }

    const injection = results[0];
    if (!injection || !Object.prototype.hasOwnProperty.call(injection, "result")) {
        return {
            ok: false,
            message: "Injected script result was discarded before reaching the extension",
            summary: summarizeInjectedResult(results),
        };
    }

    const result = injection.result;
    if (typeof result === "string") {
        const token = result.trim();
        if (!token) {
            return {
                ok: false,
                message: "Injected script returned an empty legacy token",
                summary: summarizeInjectedResult(results),
            };
        }
        if (expectsFlowResponse) {
            return {
                ok: false,
                message: "Injected script returned only a legacy token; reload the extension to enable Flow requests",
                summary: summarizeInjectedResult(results),
            };
        }
        return {
            ok: true,
            response: { status: "success", token },
            legacy: true,
            summary: "legacy_string_token",
        };
    }

    if (!result || typeof result !== "object") {
        return {
            ok: false,
            message: "Injected script returned an empty result",
            summary: summarizeInjectedResult(results),
        };
    }

    const diagnostics = result.diagnostics && typeof result.diagnostics === "object"
        ? result.diagnostics
        : {};
    const phase = diagnostics.phase || result.phase || "unknown";
    if (result.status === "error" || result.error || result.ok === false) {
        return {
            ok: false,
            message: String(result.error || "Injected script reported an error"),
            phase,
            errorCode: String(result.error_code || ""),
            cache: String(diagnostics.cache || ""),
            pageUrl: diagnostics.page_url || "",
            summary: summarizeInjectedResult(results),
        };
    }

    // 命中在途缓存时不会重新取码，因此没有 token；此时以 flow_response 为准。
    const servedFromCache = String(diagnostics.cache || "").startsWith("hit_");
    const token = typeof result.token === "string" ? result.token.trim() : "";
    if (!token && !servedFromCache) {
        return {
            ok: false,
            message: "Injected script returned an object without a token",
            phase,
            pageUrl: diagnostics.page_url || "",
            summary: summarizeInjectedResult(results),
        };
    }
    if (expectsFlowResponse && (!result.flow_response || typeof result.flow_response !== "object")) {
        return {
            ok: false,
            message: "Injected script returned a token but no Flow response; reload the extension",
            phase,
            pageUrl: diagnostics.page_url || "",
            summary: summarizeInjectedResult(results),
        };
    }
    return {
        ok: true,
        response: { status: "success", ...result, token },
        summary: summarizeInjectedResult(results),
    };
}

async function describeTabState(tabId) {
    if (!tabId) return "tab=unknown";
    try {
        const tab = await getTab(tabId);
        return `tab_status=${tab.status || "unknown"}, tab_url=${redactRuntimeText(tab.url || "unknown")}`;
    } catch (e) {
        return `tab_unavailable=${redactRuntimeText(e.message || e)}`;
    }
}

async function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    await runtimeStateReady;
    const settings = await getSettings();
    const url = new URL(settings.serverUrl || DEFAULT_SETTINGS.serverUrl);
    if (settings.apiKey) {
        url.searchParams.set("key", settings.apiKey);
    }
    if (settings.routeKey) {
        url.searchParams.set("route_key", settings.routeKey);
    }
    if (settings.clientLabel) {
        url.searchParams.set("client_label", settings.clientLabel);
    }

    // 异步更新连接状态，不阻塞 WebSocket 回调绑定
    void updateRuntimeState({
        status: "connecting",
        currentTask: null,
        lastError: "",
    }).catch(() => {});
    void appendRuntimeLog("info", `正在连接 Flow2API：${url.origin}${url.pathname}`).catch(() => {});

    const socket = new WebSocket(url.toString());
    ws = socket;
    connectionWatchdog = setTimeout(() => {
        if (ws !== socket || socket.readyState !== WebSocket.CONNECTING) return;

        console.warn("[Flow2API] WebSocket connection attempt timed out.");
        ws = null;
        connectionWatchdog = null;
        try {
            socket.close();
        } catch (e) {
            console.log("[Flow2API] Close timed-out socket error", e);
        }
        void updateRuntimeState({
            status: "disconnected",
            currentTask: null,
            lastError: `WebSocket 连接尝试超过 ${CONNECTION_ATTEMPT_TIMEOUT_MS / 1000} 秒未完成`,
        }).catch(() => {});
        scheduleReconnect("WebSocket 连接尝试超时");
    }, CONNECTION_ATTEMPT_TIMEOUT_MS);

    const handleOpen = async () => {
        if (ws !== socket) return;
        if (connectionWatchdog) {
            clearTimeout(connectionWatchdog);
            connectionWatchdog = null;
        }
        reconnectAttempt = 0;
        if (reconnectTimeout) {
            clearTimeout(reconnectTimeout);
            reconnectTimeout = null;
        }
        console.log("[Flow2API] Background connected to WebSocket", url.toString());
        await updateRuntimeState({
            status: "connected",
            lastError: "",
            lastConnectedAt: Date.now(),
        });
        await appendRuntimeLog("info", `WebSocket 已连接，Route Key：${settings.routeKey || "(empty)"}`);
        sendSocketMessage(socket, {
            type: "register",
            route_key: settings.routeKey,
            client_label: settings.clientLabel
        });
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        heartbeatInterval = setInterval(() => {
            if (socket.readyState === WebSocket.OPEN) {
                sendSocketMessage(socket, { type: "ping" });
                void updateRuntimeState({ status: "connected" }).catch(() => {});
            }
        }, 20000);
    };

    socket.onopen = () => {
        void handleOpen();
    };

    socket.onclose = async () => {
        console.log("[Flow2API] WebSocket Closed.");
        if (ws !== socket) return;
        ws = null;
        if (connectionWatchdog) {
            clearTimeout(connectionWatchdog);
            connectionWatchdog = null;
        }
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
        await updateRuntimeState({ status: "disconnected", currentTask: null });
        scheduleReconnect();
    };

    socket.onerror = async (e) => {
        if (ws !== socket) return;
        console.log("[Flow2API] WebSocket Error", e);
        await updateRuntimeState({ status: "error", lastError: "WebSocket 连接错误" });
        await appendRuntimeLog("error", "WebSocket 连接错误");
    };

    socket.onmessage = async (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        if (data.type === "pong") {
            // 心跳响应
            return;
        }

        if (data.type === "reload_extension") {
            console.log("[Flow2API] Reloading extension as requested by server...");
            void (async () => {
                try {
                    await appendRuntimeLog("info", "收到服务端热载指令，正在重新加载扩展...");
                } catch (e) {}
                try {
                    chrome.runtime.reload();
                } catch (e) {
                    console.error("[Flow2API] Failed to reload extension:", e);
                }
            })();
            return;
        }

        if (data.type === "register_ack") {
            console.log("[Flow2API] Registered route key:", data.route_key || "(empty)");
            await appendRuntimeLog("info", `已注册扩展路由：${data.route_key || "(empty)"}`);
            return;
        }

        if (data.type === "refresh_session_token") {
            void (async () => {
                await acquireTaskSlot();
                try {
                    await handleRefreshSessionToken(data, socket);
                } catch (err) {
                    console.error("[Flow2API] Refresh ST Error:", err);
                    await updateRuntimeState({ status: "error", lastError: redactRuntimeText(err.message || err) });
                    await appendRuntimeLog("error", `刷新 Session Token 异常：${err.message || err}`);
                } finally {
                    releaseTaskSlot();
                }
            })();
            return;
        }

        if (data.type === "get_token" || data.type === "submit_flow_request") {
            void (async () => {
                await acquireTaskSlot();
                try {
                    await handleGetToken(data, socket);
                } catch (err) {
                    console.error("[Flow2API] Task Error:", err);
                    await updateRuntimeState({ status: "error", lastError: redactRuntimeText(err.message || err) });
                    await appendRuntimeLog("error", `任务异常：${err.message || err}`);
                } finally {
                    releaseTaskSlot();
                }
            })();
        }
    };

    // 如果创建后已经是 OPEN 状态（本地极速连接情况），立即触发 handleOpen
    if (socket.readyState === WebSocket.OPEN) {
        void handleOpen();
    }
}

async function handleGetToken(data, socket) {
    let newTabId = null;
    let retainTabForBatchId = "";
    const isFlowRequest = data.type === "submit_flow_request";
    const batchId = isFlowRequest
        ? String((data.flow_request && data.flow_request.batch_id) || "").trim()
        : "";
    const projectId = String(data.project_id || "").trim();
    const taskLabel = isFlowRequest ? "Flow 请求" : "reCAPTCHA 取码";
    const reqId = String(data.req_id || `req_${Date.now()}_${Math.random().toString(36).slice(2)}`);
    try {
        await recordTaskStart(reqId, {
            type: taskLabel,
            action: data.action || "IMAGE_GENERATION",
            projectId: projectId || "",
            startedAt: new Date().toISOString(),
        });
        await appendRuntimeLog("info", `开始${taskLabel}：${data.action || "IMAGE_GENERATION"}${projectId ? `，project_id=${projectId}` : ""} [并发数: ${activeTasks.size}]`);
        const flowUrl = projectId
            ? `https://flow.google.com/project/${encodeURIComponent(projectId)}`
            : "https://flow.google.com/";
        // 同一 batch_id 的重试优先回到被保留的那个标签页，页内缓存就在那里。
        const retainedTabId = batchId ? await takeRetainedTab(batchId) : null;
        let flowTab;
        let isSharedTab = false;
        if (retainedTabId) {
            newTabId = retainedTabId;
            isSharedTab = true;
            await appendRuntimeLog("info", `复用保留的 Flow 标签页认领在途结果：batch_id=${batchId}`);
            flowTab = await getTab(newTabId);
        } else {
            const existingTab = await findExistingFlowTab(projectId);
            if (existingTab && existingTab.id) {
                newTabId = existingTab.id;
                isSharedTab = true;
                await appendRuntimeLog("info", `复用已打开的 Flow 页面执行任务：tab_id=${newTabId}`);
                flowTab = existingTab;
                if (flowTab.status !== "complete") {
                    await waitForTabReady(newTabId);
                }
            } else {
                console.log("[Flow2API] Opening Flow project tab for reCAPTCHA:", flowUrl);
                await appendRuntimeLog("info", `打开 Flow 项目页：${flowUrl}`);
                const newTab = await chrome.tabs.create({ url: flowUrl, active: false });
                newTabId = newTab.id;
                try {
                    await chrome.tabs.update(newTabId, { autoDiscardable: false });
                } catch (e) {}

                await waitForTabReady(newTabId);
                flowTab = await waitForFlowPage(newTabId, projectId);
                await appendRuntimeLog("info", "Flow 项目页已就绪，开始执行 reCAPTCHA");
                await sleep(1500);
            }
        }

        let successResponse = null;
        let lastErrorMsg = "Injected script did not return a result.";
        let lastErrorPhase = "execute_script";
        let lastErrorCode = "extension_script_failed";
        let resultSummary = "results=unknown";
        const scriptTimeoutMs = data.action === "VIDEO_GENERATION" ? 50000 : 40000;

        for (let injectionAttempt = 0; injectionAttempt < 2; injectionAttempt++) {
            try {
                await appendRuntimeLog("info", `开始注入 Flow 脚本：${await describeTabState(newTabId)}`);
                const results = await chrome.scripting.executeScript({
                    target: { tabId: newTabId },
                    world: "MAIN",
                    func: async (action, timeoutMs, flowRequest) => {
                    const flowTimeoutMs = flowRequest
                        ? Math.max(5000, Number(flowRequest.timeout_ms) || 40000)
                        : 0;
                    const overallTimeoutMs = flowRequest
                        ? Math.max(60000, timeoutMs + flowTimeoutMs + 5000)
                        : timeoutMs;
                    const diagnostics = {
                        phase: "start",
                        page_url: location.href || "",
                    };
                    const failure = (phase, errorCode, message, extra = {}) => ({
                        ok: false,
                        error_code: errorCode,
                        error: String(message || "Unknown injected script error"),
                        diagnostics: {
                            ...diagnostics,
                            ...extra,
                            phase,
                            page_url: location.href || diagnostics.page_url || "",
                        },
                    });
                    const withTimeout = (promise, durationMs, message) => Promise.race([
                        promise,
                        new Promise((resolve) => setTimeout(
                            () => resolve(failure("overall_timeout", "injected_script_timeout", message)),
                            durationMs,
                        )),
                    ]);

                    // 0. 防御 Google Flow 反插件 honeypot (extension_hijack_detected)
                    try {
                        const origAssign = Object.assign;
                        if (!Object.__flow2api_assign_hooked) {
                            Object.assign = function(target, ...sources) {
                                const cleaned = sources.map(src => {
                                    if (src && typeof src === "object" && src.action === "extension_hijack_detected") {
                                        const copy = origAssign({}, src);
                                        delete copy.action;
                                        return copy;
                                    }
                                    return src;
                                });
                                return origAssign.apply(Object, [target, ...cleaned]);
                            };
                            Object.__flow2api_assign_hooked = true;
                        }
                    } catch (e) {}

                    try {
                        const mod = window.default_AiSandboxAngularFrontend;
                        if (mod && mod.cG && mod.cG.prototype) {
                            Object.defineProperty(mod.cG.prototype, "Aa", {
                                get: () => false,
                                set: () => {},
                                configurable: true,
                                enumerable: true,
                            });
                        }
                    } catch (e) {}

                    // 在途请求缓存挂在页面上：fetch 本身运行在页面上下文，
                    // service worker 侧的 Map 存不住它。键为 Python 下发的
                    // batch_id，跨内外两层重试稳定。
                    const CACHE_TTL_MS = 10 * 60 * 1000;
                    const CACHE_MAX_ENTRIES = 32;
                    const getCache = () => {
                        if (!window.__flow2apiBatchCache) {
                            window.__flow2apiBatchCache = new Map();
                        }
                        const cache = window.__flow2apiBatchCache;
                        const now = Date.now();
                        for (const [key, entry] of cache) {
                            if (now - entry.createdAt > CACHE_TTL_MS) cache.delete(key);
                        }
                        while (cache.size > CACHE_MAX_ENTRIES) {
                            cache.delete(cache.keys().next().value);
                        }
                        return cache;
                    };
                    // baseResult 携带 token/fingerprint/session_cookies；命中缓存时
                    // 走缓存里存的那一份，保持出口指纹与原请求一致。
                    const buildFlowSuccess = (flowResponse, baseResult, extra) => ({
                        ok: true,
                        ...(baseResult || {}),
                        flow_response: flowResponse,
                        diagnostics: {
                            ...diagnostics,
                            phase: "completed",
                            flow_http_status: flowResponse.status,
                            page_url: location.href || diagnostics.page_url || "",
                            ...extra,
                        },
                    });

                    const run = async () => {
                        const batchId = flowRequest ? String(flowRequest.batch_id || "").trim() : "";
                        if (batchId) {
                            const cached = getCache().get(batchId);
                            if (cached && cached.state === "done") {
                                diagnostics.phase = "flow_cache_hit";
                                return buildFlowSuccess(cached.flowResponse, cached.baseResult, { cache: "hit_done" });
                            }
                            if (cached && cached.state === "in_flight") {
                                // 上一轮的 fetch 还在跑，挂上去等，不重新取码也不重发请求。
                                diagnostics.phase = "flow_cache_wait";
                                const settled = await Promise.race([
                                    cached.promise.then((flowResponse) => ({ flowResponse })).catch((error) => ({ error })),
                                    new Promise((resolve) => setTimeout(() => resolve({ pending: true }), flowTimeoutMs)),
                                ]);
                                if (settled.flowResponse) {
                                    return buildFlowSuccess(settled.flowResponse, cached.baseResult, { cache: "hit_in_flight" });
                                }
                                if (settled.pending) {
                                    return failure(
                                        "flow_fetch",
                                        "flow_fetch_in_progress",
                                        "Flow request still running in page; retry with the same batchId to claim it",
                                        { cache: "wait_pending" },
                                    );
                                }
                                getCache().delete(batchId);
                            }
                        }

                        diagnostics.phase = "recaptcha_load";
                        // 1. Flow 项目页自身会加载 reCAPTCHA Enterprise，先等待页面自带的脚本就绪
                        if (typeof grecaptcha === "undefined" || !grecaptcha || !grecaptcha.enterprise) {
                            const waitDeadline = Date.now() + 20000;
                            while (Date.now() < waitDeadline) {
                                if (typeof grecaptcha !== "undefined" && grecaptcha && grecaptcha.enterprise) {
                                    break;
                                }
                                await new Promise((r) => setTimeout(r, 200));
                            }
                        }

                        // 2. 如果页面 DOM 中已存在 recaptcha 脚本标签，等待其加载完成
                        if (typeof grecaptcha === "undefined" || !grecaptcha || !grecaptcha.enterprise) {
                            const existingScript = document.querySelector('script[src*="recaptcha/enterprise.js"]');
                            if (existingScript) {
                                await new Promise((resolve) => {
                                    if (typeof grecaptcha !== "undefined" && grecaptcha && grecaptcha.enterprise) {
                                        resolve();
                                        return;
                                    }
                                    existingScript.addEventListener("load", resolve, { once: true });
                                    setTimeout(resolve, 5000);
                                });
                            }
                        }

                        // 3. 若仍未就绪，在遵守 Trusted Types CSP 的前提下动态创建 script
                        if (typeof grecaptcha === "undefined" || !grecaptcha || !grecaptcha.enterprise) {
                            try {
                                await new Promise((resolve, reject) => {
                                    const script = document.createElement("script");
                                    const rawUrl = "https://www.google.com/recaptcha/enterprise.js?render=6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV";
                                    let scriptUrl = rawUrl;

                                    if (window.trustedTypes) {
                                        try {
                                            if (window.trustedTypes.defaultPolicy && typeof window.trustedTypes.defaultPolicy.createScriptURL === "function") {
                                                scriptUrl = window.trustedTypes.defaultPolicy.createScriptURL(rawUrl);
                                            }
                                        } catch (e) {}

                                        if (typeof scriptUrl === "string" && typeof window.trustedTypes.createPolicy === "function") {
                                            for (const policyName of ["flow2api#recaptcha", "recaptcha", "goog#html", "default"]) {
                                                try {
                                                    const p = window.trustedTypes.createPolicy(policyName, {
                                                        createScriptURL: (u) => u,
                                                    });
                                                    if (p && typeof p.createScriptURL === "function") {
                                                        scriptUrl = p.createScriptURL(rawUrl);
                                                        break;
                                                    }
                                                } catch (e) {}
                                            }
                                        }
                                    }

                                    try {
                                        script.src = scriptUrl;
                                    } catch (err) {
                                        try {
                                            script.setAttribute("src", scriptUrl);
                                        } catch (err2) {
                                            reject(err);
                                            return;
                                        }
                                    }

                                    script.onload = resolve;
                                    script.onerror = () => reject(new Error("Failed to load enterprise.js via network"));
                                    if (!document.head) {
                                        reject(new Error("Flow page has no document head for enterprise.js"));
                                        return;
                                    }
                                    document.head.appendChild(script);
                                });
                            } catch (injectErr) {
                                console.debug("[Flow2API] Dynamic script injection attempt:", injectErr);
                            }
                        }

                        // 4. 最终等待确认 grecaptcha.enterprise 可用
                        if (typeof grecaptcha === "undefined" || !grecaptcha || !grecaptcha.enterprise) {
                            const finalDeadline = Date.now() + 6000;
                            while (Date.now() < finalDeadline) {
                                if (typeof grecaptcha !== "undefined" && grecaptcha && grecaptcha.enterprise) {
                                    break;
                                }
                                await new Promise((r) => setTimeout(r, 250));
                            }
                        }

                        if (typeof grecaptcha === "undefined" || !grecaptcha.enterprise) {
                            return failure("recaptcha_load", "recaptcha_unavailable", "reCAPTCHA enterprise API is unavailable");
                        }

                        diagnostics.phase = "recaptcha_ready";
                        const token = await new Promise((resolve, reject) => {
                            let settled = false;
                            const finish = (callback, value) => {
                                if (settled) return;
                                settled = true;
                                callback(value);
                            };
                            try {
                                grecaptcha.enterprise.ready(() => {
                                    diagnostics.phase = "recaptcha_execute";
                                    let execution;
                                    try {
                                        execution = grecaptcha.enterprise.execute(
                                            "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV",
                                            { action },
                                        );
                                    } catch (error) {
                                        finish(reject, error);
                                        return;
                                    }
                                    if (!execution || typeof execution.then !== "function") {
                                        finish(reject, new Error("reCAPTCHA execute returned a non-Promise result"));
                                        return;
                                    }
                                    execution.then(
                                        (value) => finish(resolve, value),
                                        (error) => finish(reject, error),
                                    );
                                });
                            } catch (error) {
                                finish(reject, error);
                            }
                        });

                        if (typeof token !== "string" || !token.trim()) {
                            return failure(
                                "recaptcha_execute",
                                "empty_recaptcha_token",
                                "reCAPTCHA returned an empty token",
                            );
                        }

                        const uaData = navigator.userAgentData || null;
                        let highEntropy = {};
                        try {
                            if (uaData && typeof uaData.getHighEntropyValues === "function") {
                                highEntropy = await uaData.getHighEntropyValues([
                                    "platform",
                                    "platformVersion",
                                    "architecture",
                                    "model",
                                    "uaFullVersion",
                                ]);
                            }
                        } catch (e) {
                            console.debug("[Flow2API] Could not read high entropy UA data", e);
                        }
                        const brands = uaData && Array.isArray(uaData.brands) ? uaData.brands : [];
                        const secChUa = brands.map(item => `"${item.brand}";v="${item.version}"`).join(", ");
                        const result = {
                            token: token.trim(),
                            fingerprint: {
                                user_agent: navigator.userAgent || "",
                                accept_language: navigator.languages && navigator.languages.length
                                    ? navigator.languages.join(",")
                                    : (navigator.language || ""),
                                sec_ch_ua: secChUa,
                                sec_ch_ua_mobile: uaData && uaData.mobile ? "?1" : "?0",
                                sec_ch_ua_platform: `"${highEntropy.platform || (uaData && uaData.platform) || ""}"`,
                                page_url: location.href || "",
                                origin: location.origin || "https://labs.google",
                            },
                            diagnostics: {
                                ...diagnostics,
                                phase: "recaptcha_complete",
                                page_url: location.href || diagnostics.page_url || "",
                            },
                        };
                        if (!flowRequest) {
                            return { ok: true, ...result };
                        }

                        diagnostics.phase = "flow_request_validation";
                        let targetUrl;
                        try {
                            targetUrl = new URL(String(flowRequest.url || ""));
                        } catch (e) {
                            return failure("flow_request_validation", "invalid_flow_url", "Invalid Flow request URL");
                        }
                        if (
                            targetUrl.protocol !== "https:" ||
                            (targetUrl.hostname !== "googleapis.com" && !targetUrl.hostname.endsWith(".googleapis.com"))
                        ) {
                            return failure(
                                "flow_request_validation",
                                "disallowed_flow_url",
                                "Flow request URL is not an allowed Google API endpoint",
                            );
                        }

                        diagnostics.phase = "flow_payload_patch";
                        const payload = typeof structuredClone === "function"
                            ? structuredClone(flowRequest.json_data)
                            : JSON.parse(JSON.stringify(flowRequest.json_data));
                        const patchToken = (value) => {
                            if (!value) return;
                            if (Array.isArray(value)) {
                                value.forEach(patchToken);
                                return;
                            }
                            if (typeof value !== "object") return;
                            if (value.recaptchaContext && typeof value.recaptchaContext === "object") {
                                value.recaptchaContext.token = token.trim();
                                if (!value.recaptchaContext.applicationType) {
                                    value.recaptchaContext.applicationType = "RECAPTCHA_APPLICATION_TYPE_WEB";
                                }
                            }
                            Object.values(value).forEach(patchToken);
                        };
                        patchToken(payload);

                        diagnostics.phase = "flow_fetch";
                        const requestTimeoutMs = Math.max(5000, Number(flowRequest.timeout_ms) || timeoutMs);
                        // 不再 abort：上游可能已经在生成，abort 只会丢掉结果而不会
                        // 取消生成。到点先返回软超时，fetch 继续跑并把结果写进缓存。
                        const fetchPromise = (async () => {
                            const response = await fetch(targetUrl.toString(), {
                                method: "POST",
                                headers: {
                                    authorization: `Bearer ${String(flowRequest.at_token || "")}`,
                                    "content-type": "text/plain;charset=UTF-8",
                                },
                                credentials: "include",
                                body: JSON.stringify(payload),
                            });
                            const text = await response.text();
                            const responseHeaders = {};
                            response.headers.forEach((value, key) => {
                                responseHeaders[key] = value;
                            });
                            return {
                                ok: response.ok,
                                status: response.status,
                                text,
                                headers: responseHeaders,
                            };
                        })();

                        if (batchId) {
                            const cache = getCache();
                            const entry = {
                                state: "in_flight",
                                createdAt: Date.now(),
                                promise: fetchPromise,
                                flowResponse: null,
                                baseResult: result,
                            };
                            cache.set(batchId, entry);
                            fetchPromise.then(
                                (flowResponse) => {
                                    entry.state = "done";
                                    entry.flowResponse = flowResponse;
                                },
                                () => {
                                    cache.delete(batchId);
                                },
                            );
                        }

                        const settled = await Promise.race([
                            fetchPromise.then((flowResponse) => ({ flowResponse })),
                            new Promise((resolve) => setTimeout(() => resolve({ pending: true }), requestTimeoutMs)),
                        ]);
                        if (settled.pending) {
                            return failure(
                                "flow_fetch",
                                batchId ? "flow_fetch_in_progress" : "flow_fetch_timeout",
                                batchId
                                    ? "Flow request still running in page; retry with the same batchId to claim it"
                                    : "flow_fetch_timeout",
                                { cache: batchId ? "stored_in_flight" : "disabled" },
                            );
                        }
                        diagnostics.phase = "flow_response";
                        return buildFlowSuccess(settled.flowResponse, result, batchId ? { cache: "miss_completed" } : {});
                    };

                    try {
                        return await withTimeout(
                            run(),
                            overallTimeoutMs,
                            flowRequest ? "Timeout executing Chrome Flow request" : "Timeout generating reCAPTCHA locally",
                        );
                    } catch (error) {
                        const message = error && error.message ? error.message : String(error || "Unknown injected script error");
                        return failure(diagnostics.phase || "unknown", "injected_script_failed", message);
                    }
                },
                args: [data.action || "IMAGE_GENERATION", scriptTimeoutMs, data.flow_request || null]
            });

            const normalized = normalizeInjectedResult(results, isFlowRequest);
            resultSummary = normalized.summary || summarizeInjectedResult(results);
            if (normalized.ok) {
                successResponse = normalized.response;
                await appendRuntimeLog("info", `注入脚本完成：${resultSummary}`);
                break;
            } else {
                lastErrorMsg = normalized.message;
                lastErrorPhase = normalized.phase || "result_normalization";
                lastErrorCode = normalized.errorCode || "extension_script_failed";
                if (batchId && normalized.errorCode === "flow_fetch_in_progress") {
                    retainTabForBatchId = batchId;
                }
                if (
                    (lastErrorCode === "recaptcha_poisoned" || String(lastErrorMsg).includes("extension_hijack"))
                    && injectionAttempt === 0
                ) {
                    await appendRuntimeLog("warn", "检测到页面中存在反插件劫持 (extension_hijack_detected)，正在刷新标签页以应用防护脚本...");
                    await chrome.tabs.reload(newTabId);
                    await waitForTabReady(newTabId);
                    flowTab = await waitForFlowPage(newTabId, projectId);
                    await sleep(1500);
                    continue;
                }
                await appendRuntimeLog(
                    "error",
                    `注入脚本失败：phase=${lastErrorPhase}，${normalized.message}，${resultSummary}`,
                );
                break;
            }
        } catch (e) {
            lastErrorMsg = e.message || "Script execution failed";
            lastErrorPhase = "execute_script";
            resultSummary = `executeScript_error=${redactRuntimeText(lastErrorMsg)}`;
            await appendRuntimeLog(
                "error",
                `executeScript 异常：${redactRuntimeText(lastErrorMsg)}，${await describeTabState(newTabId)}`,
            );
            break;
        }
        }

        if (successResponse) {
            await appendRuntimeLog(
                successResponse.flow_response && successResponse.flow_response.status >= 400 ? "error" : "info",
                successResponse.flow_response
                    ? `Flow 请求完成：HTTP ${successResponse.flow_response.status}`
                    : "reCAPTCHA token 获取成功",
            );
            sendSocketMessage(socket, {
                req_id: data.req_id,
                status: successResponse.status,
                token: successResponse.token,
                fingerprint: successResponse.fingerprint,
                flow_response: successResponse.flow_response,
                session_cookies: await collectRuntimeCookies(),
                page_url: flowTab.url || ""
            });
        } else {
            const safeError = redactRuntimeText(
                `Extension script failed at ${lastErrorPhase}: ${lastErrorMsg} (${resultSummary}; ${await describeTabState(newTabId)})`,
            );
            await appendRuntimeLog("error", safeError);
            sendSocketMessage(socket, {
                req_id: data.req_id,
                status: "error",
                error: safeError,
                error_code: lastErrorCode,
                phase: lastErrorPhase,
                diagnostics: { summary: resultSummary, batch_id: batchId },
            });
        }
    } catch (err) {
        const safeError = redactRuntimeText(err.message || err);
        await appendRuntimeLog("error", `${taskLabel}失败：${safeError}`);
        sendSocketMessage(socket, {
            req_id: data.req_id,
            status: "error",
            error: safeError,
            error_code: "extension_task_failed",
            phase: "task",
        });
    } finally {
        await recordTaskEnd(reqId);
        if (newTabId && retainTabForBatchId) {
            // 页内 fetch 还在跑，缓存挂在这个页面上；关掉标签页等于丢结果。
            // 留着让重试用同一个 batchId 回来认领，到点由清理器兜底关闭。
            scheduleRetainedTabCleanup(retainTabForBatchId, newTabId);
            await appendRuntimeLog(
                "info",
                `Flow 请求仍在页内执行，保留标签页以便重试认领结果：batch_id=${retainTabForBatchId}`,
            );
        } else if (newTabId && !isSharedTab) {
            try {
                await chrome.tabs.remove(newTabId);
                console.log("[Flow2API] Closed temporary token tab.");
            } catch (e) {
                console.log("[Flow2API] Error closing tab:", e);
            }
        }
    }
}

async function handleRefreshSessionToken(data, socket) {
    const oldSt = String(data.old_st || "").trim();
    const email = String(data.email || "").trim();
    const reqId = String(data.req_id || `req_${Date.now()}_${Math.random().toString(36).slice(2)}`);
    let tempTabId = null;
    let didCreateTab = false;

    try {
        await recordTaskStart(reqId, {
            type: "刷新 Session Token",
            action: "SESSION_REFRESH",
            projectId: "",
            startedAt: new Date().toISOString(),
        });
        await appendRuntimeLog("info", `开始自动刷新 Session Token${email ? ` (${email})` : ""} [并发数: ${activeTasks.size}]`);

        // 1. 检查现有 Cookie：必须同时满足以下条件才能直接复用：
        //    a) tokenCandidate 不能与服务端已判定失效的 oldSt 相同；
        //    b) session 响应不能包含 error (如 ACCESS_TOKEN_REFRESH_NEEDED)；
        //    c) expires 必须在未来至少 5 分钟之后 (未过期)。
        const existingTokens = await getAllNextAuthSessionCookies();
        for (const tokenCandidate of existingTokens) {
            if (oldSt && tokenCandidate === oldSt) {
                console.log("[Flow2API] Candidate cookie matches old_st (known expired/stale), skipping reuse");
                continue;
            }
            try {
                const sessResp = await fetch("https://labs.google/fx/api/auth/session", { credentials: "include" });
                if (sessResp.ok) {
                    const parsed = await sessResp.json();
                    const expiresTime = parsed && parsed.expires ? new Date(parsed.expires).getTime() : 0;
                    const isFuture = expiresTime > (Date.now() + 5 * 60 * 1000);
                    const hasError = Boolean(parsed && parsed.error);

                    if (parsed && parsed.access_token && !hasError && isFuture) {
                        await appendRuntimeLog("info", "检测到现有浏览器中存在有效新 Session，直接复用");
                        await updateRuntimeState({ status: "connected", currentTask: null, lastError: "" });
                        sendSocketMessage(socket, {
                            req_id: data.req_id,
                            status: "success",
                            session_token: tokenCandidate,
                            access_token: parsed.access_token,
                            expires: parsed.expires || null,
                        });
                        return;
                    } else if (hasError || !isFuture) {
                        console.log("[Flow2API] Existing session is invalid/expired:", { error: parsed && parsed.error, expires: parsed && parsed.expires });
                    }
                }
            } catch (e) {
                console.debug("[Flow2API] Failed to verify existing session:", e);
            }
        }

        // 2. 清除浏览器中已失效的旧 NextAuth Session Cookie，确保重新走 OAuth 时写入最新会话
        try {
            const domainUrls = ["https://labs.google/", "https://labs.google/fx", "https://flow.google.com/"];
            for (const u of domainUrls) {
                for (const n of ["__Secure-next-auth.session-token", "next-auth.session-token"]) {
                    await new Promise(res => chrome.cookies.remove({ url: u, name: n }, res));
                }
            }
        } catch (e) {}

        // 3. 现有会话无效或缺少 Cookie，通过 NextAuth 授权激活新会话
        const targetUrl = await triggerLabsNextAuthSignIn(email);
        await appendRuntimeLog("info", "打开静默认证页建立 Labs 会话...");
        const tab = await chrome.tabs.create({ url: targetUrl, active: false });
        tempTabId = tab.id;
        didCreateTab = true;
        try {
            await chrome.tabs.update(tempTabId, { autoDiscardable: false });
        } catch (e) {}

        await waitForTabReady(tempTabId, 15000);

        // 轮询等待新 Cookie 生成并验证有效性
        const deadline = Date.now() + 25000;
        let newSt = null;
        let sessionData = null;
        while (Date.now() < deadline) {
            const candidateSt = await getNextAuthSessionCookie();
            if (candidateSt && (!oldSt || candidateSt !== oldSt)) {
                try {
                    const sessResp = await fetch("https://labs.google/fx/api/auth/session", { credentials: "include" });
                    if (sessResp.ok) {
                        const parsed = await sessResp.json();
                        const expiresTime = parsed && parsed.expires ? new Date(parsed.expires).getTime() : 0;
                        const isFuture = expiresTime > (Date.now() + 5 * 60 * 1000);
                        const hasError = Boolean(parsed && parsed.error);

                        if (parsed && parsed.access_token && !hasError && isFuture) {
                            newSt = candidateSt;
                            sessionData = parsed;
                            break;
                        }
                    }
                } catch (e) {}
            }
            await sleep(600);
        }

        if (newSt) {
            try {
                const sessResp = await fetch("https://labs.google/fx/api/auth/session", { credentials: "include" });
                if (sessResp.ok) {
                    sessionData = await sessResp.json();
                }
            } catch (e) {
                console.debug("[Flow2API] Failed to read refreshed session:", e);
            }

            await appendRuntimeLog("info", "Session Token 自动刷新成功并已提取");
            await updateRuntimeState({ status: "connected", currentTask: null, lastError: "" });
            sendSocketMessage(socket, {
                req_id: data.req_id,
                status: "success",
                session_token: newSt,
                access_token: sessionData && sessionData.access_token ? sessionData.access_token : null,
                expires: sessionData && sessionData.expires ? sessionData.expires : null,
            });
            return;
        }

        const err = "未能获取到 __Secure-next-auth.session-token，请确认当前 Chrome 浏览器已登录 Google 账号";
        await appendRuntimeLog("error", err);
        await updateRuntimeState({ status: "error", currentTask: null, lastError: err });
        sendSocketMessage(socket, {
            req_id: data.req_id,
            status: "error",
            error: err,
        });

    } catch (err) {
        const errorMsg = redactRuntimeText(err.message || String(err));
        await appendRuntimeLog("error", `刷新 Session Token 异常：${errorMsg}`);
        await updateRuntimeState({ status: "error", currentTask: null, lastError: errorMsg });
        sendSocketMessage(socket, {
            req_id: data.req_id,
            status: "error",
            error: errorMsg,
        });
    } finally {
        await recordTaskEnd(reqId);
        if (tempTabId && didCreateTab) {
            try {
                await chrome.tabs.remove(tempTabId);
                console.log("[Flow2API] Closed temporary auth tab.");
            } catch (e) {
                console.debug("[Flow2API] Error closing auth tab:", e);
            }
        }
    }
}

chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.routeKey || changes.serverUrl || changes.apiKey || changes.clientLabel) {
        console.log("[Flow2API] Extension settings changed, reconnecting WebSocket...");
        closeSocket();
        connectWS().catch(async (error) => {
            const safeError = redactRuntimeText(error.message || error);
            await updateRuntimeState({ status: "error", lastError: safeError });
            await appendRuntimeLog("error", `配置变更后连接失败：${safeError}`);
            scheduleReconnect("配置变更后连接失败");
        });
    }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || message.type !== "reconnect") return false;
    closeSocket();
    connectWS().catch(async (error) => {
        const safeError = redactRuntimeText(error.message || error);
        await updateRuntimeState({ status: "error", lastError: safeError });
        await appendRuntimeLog("error", `重新连接失败：${safeError}`);
        scheduleReconnect("重新连接失败");
    });
    sendResponse({ ok: true });
    return true;
});

const TOKEN_SYNC_ALARM = "flow2api_token_auto_sync";

async function setupTokenSyncAlarm() {
    try {
        await chrome.alarms.clear(TOKEN_SYNC_ALARM);
        chrome.alarms.create(TOKEN_SYNC_ALARM, { periodInMinutes: 15 });
        console.log("[Flow2API] Token auto-sync alarm configured (15m)");
    } catch (e) {
        console.debug("[Flow2API] Failed to setup alarm:", e);
    }
}

async function performAutoTokenSync() {
    try {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const tokens = await getAllNextAuthSessionCookies();
        if (!tokens || tokens.length === 0) return;
        const sessResp = await fetch("https://labs.google/fx/api/auth/session", { credentials: "include" });
        if (!sessResp.ok) return;
        const parsed = await sessResp.json();
        if (!parsed || !parsed.access_token) return;

        const settings = await getSettings();
        sendSocketMessage(ws, {
            type: "sync_session_token",
            route_key: settings.routeKey || "",
            client_label: settings.clientLabel || "",
            session_token: tokens[0],
            access_token: parsed.access_token,
            expires: parsed.expires || null,
            email: (parsed.user && parsed.user.email) || "",
        });
        console.log("[Flow2API] Proactively synced session token to server");
    } catch (e) {
        console.debug("[Flow2API] Auto token sync error:", e);
    }
}

if (chrome.alarms && chrome.alarms.onAlarm) {
    chrome.alarms.onAlarm.addListener((alarm) => {
        if (alarm && alarm.name === TOKEN_SYNC_ALARM) {
            void performAutoTokenSync();
        }
    });
}

connectWS().catch(async (error) => {
    const safeError = redactRuntimeText(error.message || error);
    await updateRuntimeState({ status: "error", lastError: safeError });
    await appendRuntimeLog("error", `初始化连接失败：${safeError}`);
    scheduleReconnect("初始化连接失败");
});

void registerProtectionScript();
void setupTokenSyncAlarm();
