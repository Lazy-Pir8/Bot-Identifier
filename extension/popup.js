/**
 * extension/popup.js — Status controller for popup
 */

const API_STATUS = "http://127.0.0.1:8000/api/status";

async function checkServer() {
  const dot = document.getElementById("statusDot");
  const text = document.getElementById("statusText");

  try {
    const res = await fetch(API_STATUS, { method: "GET" });
    if (res.ok) {
      dot.className = "dot dot-online";
      text.innerText = "Online";
      text.style.color = "#10b981";
    } else {
      dot.className = "dot dot-offline";
      text.innerText = "Error";
      text.style.color = "#ef4444";
    }
  } catch (err) {
    dot.className = "dot dot-offline";
    text.innerText = "Offline";
    text.style.color = "#ef4444";
  }
}

// Query scan stats from background service worker or active tab
try {
  chrome.runtime.sendMessage({ type: "GET_COUNT" }, (response) => {
    if (response && typeof response.count === "number" && response.count > 0) {
      document.getElementById("labeledCount").innerText = response.count;
    } else {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]) {
          chrome.tabs.sendMessage(tabs[0].id, { type: "GET_COUNT" }, (tabResp) => {
            if (tabResp && typeof tabResp.count === "number") {
              document.getElementById("labeledCount").innerText = tabResp.count;
            } else {
              document.getElementById("labeledCount").innerText = "Active";
            }
          });
        }
      });
    }
  });
} catch (_) {
  document.getElementById("labeledCount").innerText = "Ready";
}

checkServer();
