/**
 * extension/background.js — Manifest V3 Background Service Worker
 * 
 * Proxies HTTP requests to the local backend (http://127.0.0.1:8000)
 * avoiding web page Content Security Policy (CSP), Mixed Content,
 * and Chromium Private Network Access restrictions.
 */

const API_SERVER = "http://127.0.0.1:8000";

let scannedCount = 0;

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "SCAN_USERS") {
    fetch(`${API_SERVER}/api/scan-users`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload)
    })
      .then(async (res) => {
        if (!res.ok) {
          sendResponse({ ok: false, status: res.status });
          return;
        }
        const data = await res.json();
        sendResponse({ ok: true, data });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true; // Keep sendResponse open for async fetch
  }

  if (message.type === "GET_STATUS") {
    fetch(`${API_SERVER}/api/status`)
      .then(async (res) => {
        if (!res.ok) {
          sendResponse({ ok: false, status: res.status });
          return;
        }
        const data = await res.json();
        sendResponse({ ok: true, data });
      })
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
      });
    return true;
  }

  if (message.type === "UPDATE_STATS") {
    if (typeof message.count === "number") {
      scannedCount = message.count;
    }
    sendResponse({ ok: true, count: scannedCount });
    return false;
  }

  if (message.type === "GET_COUNT") {
    sendResponse({ ok: true, count: scannedCount });
    return false;
  }
});

