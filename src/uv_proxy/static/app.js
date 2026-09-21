const form = document.querySelector("#proxy-form");
const methodInput = document.querySelector("#method");
const urlInput = document.querySelector("#url");
const headersInput = document.querySelector("#headers");
const bodyInput = document.querySelector("#body");
const sendButton = document.querySelector(".send-button");
const responsePreview = document.querySelector("#response-preview code");
const responseStatus = document.querySelector("#result-status");
const responseTabs = [...document.querySelectorAll(".tab")];
const curlPreview = document.querySelector("#curl-preview code");
const history = [];
let currentHeaders = {};
let currentBody = "Send a request to bring the response into focus.";

function parseHeaders(value) {
  return value.split("\n").reduce((result, line) => {
    const separator = line.indexOf(":");
    if (separator > 0) {
      const key = line.slice(0, separator).trim();
      const item = line.slice(separator + 1).trim();
      if (key && item) result[key] = item;
    }
    return result;
  }, {});
}

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function prettyBody(body, contentType = "") {
  if (contentType.includes("json")) {
    try { return JSON.stringify(JSON.parse(body), null, 2); } catch { /* Keep malformed JSON readable. */ }
  }
  return body || "(empty response)";
}

function updateCurl() {
  const method = methodInput.value;
  const url = urlInput.value.trim();
  const headers = parseHeaders(headersInput.value);
  const headerFlags = Object.entries(headers).map(([key, value]) => ` -H ${JSON.stringify(`${key}: ${value}`)}`).join("");
  const bodyFlag = bodyInput.value.trim() ? ` --data ${JSON.stringify(bodyInput.value.trim())}` : "";
  const methodFlag = method === "GET" ? "" : ` -X ${method}`;
  curlPreview.textContent = `curl${methodFlag}${headerFlags}${bodyFlag} ${JSON.stringify(url || "https://example.com")}`;
}

function setResultState(kind, label) {
  responseStatus.className = `result-status ${kind}`;
  responseStatus.querySelector("span:last-child").textContent = label;
}

function renderHeaders() {
  responsePreview.textContent = Object.entries(currentHeaders).map(([key, value]) => `${key}: ${value}`).join("\n") || "(no visible response headers)";
  document.querySelector("#code-label").textContent = "response headers";
}

function renderBody() {
  responsePreview.textContent = currentBody;
  document.querySelector("#code-label").textContent = "response preview";
}

function renderHistory() {
  const list = document.querySelector("#recent-list");
  document.querySelector("#request-count").textContent = history.length;
  if (!history.length) {
    list.innerHTML = '<div class="empty-recent">No requests yet — the first one sets the tempo.</div>';
    return;
  }
  list.innerHTML = history.slice(0, 4).map((item) => `
    <button class="recent-item" type="button" data-url="${item.url.replaceAll('"', '&quot;')}" data-method="${item.method}">
      <span class="recent-method">${item.method}</span><span class="recent-url">${item.url}</span><span class="recent-time">${item.time} ms</span>
    </button>`).join("");
  list.querySelectorAll(".recent-item").forEach((item) => item.addEventListener("click", () => {
    urlInput.value = item.dataset.url;
    methodInput.value = item.dataset.method;
    updateCurl();
  }));
}

async function runRequest(event) {
  event.preventDefault();
  const url = urlInput.value.trim();
  const method = methodInput.value;
  if (!url) return;

  sendButton.disabled = true;
  sendButton.querySelector(".button-label").textContent = "Finding the signal…";
  setResultState("", "Connecting");
  document.querySelector("#status-code").textContent = "…";
  document.querySelector("#status-text").textContent = "Opening a pooled connection";
  document.querySelector("#response-url").textContent = url;
  document.querySelector("#response-time").textContent = "— ms";

  try {
    const response = await fetch("/api/proxy", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url, method, headers: parseHeaders(headersInput.value), body: bodyInput.value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "The proxy could not complete that request.");

    currentHeaders = payload.headers || {};
    currentBody = prettyBody(payload.body, currentHeaders["content-type"] || "");
    renderBody();
    responseTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === "body"));
    document.querySelector("#status-code").textContent = payload.status;
    document.querySelector("#status-text").textContent = payload.status_text || "Complete";
    document.querySelector("#response-time").textContent = `${payload.elapsed_ms} ms`;
    document.querySelector("#metric-latency").textContent = `${payload.elapsed_ms} ms`;
    document.querySelector("#metric-size").textContent = formatBytes(payload.bytes_received);
    document.querySelector("#metric-method").textContent = method;
    document.querySelector("#response-note").textContent = payload.truncated ? "Preview capped at the configured body limit." : "Response received cleanly through the pooled upstream connection.";
    setResultState(payload.status >= 400 ? "error" : "success", payload.status >= 400 ? "Upstream issue" : "Connected");
    history.unshift({ url, method, time: payload.elapsed_ms });
    renderHistory();
  } catch (error) {
    currentHeaders = {};
    currentBody = error.message;
    renderBody();
    document.querySelector("#status-code").textContent = "!";
    document.querySelector("#status-text").textContent = error.message;
    document.querySelector("#response-time").textContent = "— ms";
    document.querySelector("#metric-latency").textContent = "—";
    document.querySelector("#metric-size").textContent = "—";
    setResultState("error", "Needs attention");
  } finally {
    sendButton.disabled = false;
    sendButton.querySelector(".button-label").textContent = "Send request";
  }
}

form.addEventListener("submit", runRequest);
[urlInput, methodInput, headersInput, bodyInput].forEach((input) => input.addEventListener("input", updateCurl));
responseTabs.forEach((tab) => tab.addEventListener("click", () => {
  responseTabs.forEach((item) => item.classList.toggle("active", item === tab));
  tab.dataset.tab === "headers" ? renderHeaders() : renderBody();
}));
document.querySelector("#copy-curl").addEventListener("click", async (event) => {
  await navigator.clipboard?.writeText(curlPreview.textContent);
  event.currentTarget.textContent = "Copied";
  setTimeout(() => { event.currentTarget.textContent = "Copy"; }, 1200);
});

updateCurl();
renderHistory();
