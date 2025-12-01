# main.py — Student Support Concierge (ADK routing version)
# Merged version: includes local KB fallback, fuzzy matching, and improved ask() parsing.

from flask import Flask, request, jsonify, render_template_string, send_file
import logging, io, json, time, importlib, difflib, os
from typing import Dict, Any

# ADK imports (use existing project code)
try:
    from .root_agent import root_agent, build_root_agent, GeminiLLM
except Exception:
    try:
        from student_support.root_agent import root_agent, build_root_agent, GeminiLLM  # type: ignore
    except Exception:
        root_agent = None
        build_root_agent = None
        GeminiLLM = None

# Memory store import (adapt path as needed)
try:
    from .memory import MemoryStore
except Exception:
    try:
        from student_support.memory import MemoryStore  # type: ignore
    except Exception:
        # fallback simple in-memory store
        class MemoryStore:
            def __init__(self):
                self.sessions = {}
            def create_session(self, username):
                sid = f"sess_{username}_{int(time.time())}"
                self.sessions[sid] = {"username": username, "history": []}
                return sid
            def get_session(self, sid):
                return self.sessions.get(sid)
            def add_message(self, sid, role, text):
                if sid in self.sessions:
                    self.sessions[sid].setdefault("history", []).append({"role": role, "text": text})

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

# Ensure root_agent exists (lazy build)
if 'root_agent' in globals() and root_agent is None and build_root_agent is not None:
    try:
        root_agent = build_root_agent()
        logging.info("Built root_agent lazily in web_demo.")
        try:
            subs_keys = list(getattr(root_agent, "subagents", {}) or getattr(root_agent, "sub", {}) or {})
            logging.info("root_agent subagents: %s", subs_keys)
        except Exception:
            logging.exception("Failed to list root_agent subagents")
    except Exception:
        logging.exception("Failed to build_root_agent")
        root_agent = None

# memory instance
try:
    memory = root_agent.memory if (root_agent is not None and hasattr(root_agent, "memory")) else MemoryStore()
except Exception:
    memory = MemoryStore()

# -------------------------
# Helpers: Gem status & heuristics
# -------------------------
def is_gemini_available() -> bool:
    try:
        if root_agent is None:
            return False
        subs = getattr(root_agent, "subagents", {}) or getattr(root_agent, "sub", {})
        for v in (subs or {}).values():
            if hasattr(v, "llm") and getattr(v.llm, "available", False):
                return True
        if hasattr(root_agent, "llm") and getattr(root_agent.llm, "available", False):
            return True
    except Exception:
        logging.exception("is_gemini_available check failed")
    return False

def features_for_message(agent_name: str, message: str) -> Dict[str, Any]:
    features = []
    m = (message or "").lower()
    if is_gemini_available():
        features.append("Gemini (LLM)")
    if agent_name in ("TechSupportAgent", "ProgressAgent"):
        features.append("Tools (MCP / custom)")
    if agent_name == "FAQAgent":
        features.append("Built-in FAQ / Search")
    if any(tok in m for tok in ("run code", "execute", "python", "script", "eval(")):
        features.append("Code Execution")
    if any(tok in m for tok in ("background", "long-running", "pause", "resume", "job", "process")):
        features.append("Long-running ops")
    features.append("Sessions & Memory")
    features.append("Observability (logs)")
    return {"features": features}

AGENT_AVATARS = {
    "OrientationAgent": {"emoji": "🎓", "label": "Orientation"},
    "TechSupportAgent": {"emoji": "🛠️", "label": "Tech Support"},
    "ProgressAgent": {"emoji": "📈", "label": "Progress"},
    "FAQAgent": {"emoji": "❓", "label": "FAQ"},
    "Assistant": {"emoji": "🤖", "label": "Assistant"},
    # FIXED: use ErrorAgent key (consistent with LOCAL_KB)
    "ErrorAgent": {"emoji": "⚠️", "label": "Error"},
}

# -------------------------
# LOCAL KB and fallback router (place near top)
# -------------------------
LOCAL_KB = {
    "OrientationAgent": [
        {"q": "how can i start?", "a": "To start: login to your LMS dashboard → open the Orientation module → complete lessons and the short quiz. If you can't find it, provide your username."},
        {"q": "how do i take the orientation?", "a": "Open Dashboard → Orientation → Start Module. Complete each lesson and the orientation assessment to be marked complete."},
        {"q": "where is the orientation module?", "a": "Orientation is listed under 'My Courses' or 'Course Modules'. If missing, provide your username and I'll check enrollment."},
        {"q": "do i need ms office?", "a": "No. The course is browser-based. You can use MS365 online if you want to practice offline files."}
    ],
    "TechSupportAgent": [
        {"q": "i need access code", "a": "Please give your username. We'll look for an access code on file or generate/escalate one for you."},
        {"q": "i can't log in", "a": "Try 'Forgot password' link. If that fails, give your username and any error text (e.g., 'access denied') and we'll check activation."},
        {"q": "how do i install lockdown browser?", "a": "Download the LockDown Browser installer from the LMS Help link, run the installer and then log in to the browser with your LMS credentials."},
        {"q": "access denied error", "a": "This usually means your account isn't activated. Provide username and we will resend activation or escalate to support."}
    ],
    "ProgressAgent": [
        {"q": "show my progress", "a": "Provide your username and I will return modules completed, percent complete, and pending quizzes."},
        {"q": "where am i in my course?", "a": "You are currently on module X. Provide username for exact module name and percent complete."},
        {"q": "how much percent completed?", "a": "Percent complete is computed as (completed lessons / total lessons) * 100. Provide username for exact figure."}
    ],
    "FAQAgent": [
        {"q": "what is the refund policy?", "a": "Refunds: allowed within 7 days of enrollment if < 10% course completed. Contact support with your username to submit a request."},
        {"q": "what are class timings?", "a": "Course is self-paced. Live office hours: Monday & Thursday 7–9 PM (local)."},
        {"q": "how long is the course?", "a": "Typical 8–12 weeks depending on learner pace."},
        {"q": "do i get a certificate?", "a": "Yes — on 100% completion and passing the final assessment."}
    ],
    "ErrorAgent": [
        {"q": "server error", "a": "Please copy the full error message and approximate time; we'll check server logs."},
        {"q": "app crashed", "a": "Try clearing browser cache and reloading. If it persists, tell us steps to reproduce and browser/version."}
    ]
}

def best_kb_match(agent_name: str, message: str, cutoff: float = 0.6):
    """
    Return KB answer string if close match found; else None.
    Uses simple fuzzy matching via difflib.SequenceMatcher on the question texts.
    Also tries a few common alias fallbacks to tolerate minor naming mismatches.
    """
    if not message:
        return None

    # alias fallbacks (try exact, agentNameAgent, remove Agent suffix)
    candidates = []
    if isinstance(agent_name, str):
        candidates.append(agent_name)
        if not agent_name.endswith("Agent"):
            candidates.append(agent_name + "Agent")
        if agent_name.endswith("Agent"):
            candidates.append(agent_name.replace("Agent", ""))
    # unique preserve order
    seen = set()
    final_candidates = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            final_candidates.append(c)

    # search across candidate KB lists
    message_norm = message.strip().lower()
    best = None
    best_score = 0.0
    qlist = []
    for cname in final_candidates:
        qlist = LOCAL_KB.get(cname, [])
        for entry in qlist:
            q = entry.get("q","").lower()
            score = difflib.SequenceMatcher(None, message_norm, q).ratio()
            if score > best_score:
                best_score = score
                best = entry
    if best and best_score >= cutoff:
        return best.get("a")
    # fallback: token overlap across candidate lists
    tokens = set(message_norm.split())
    for cname in final_candidates:
        for entry in LOCAL_KB.get(cname, []):
            qtokens = set(entry.get("q","").lower().split())
            if tokens and (tokens & qtokens):
                return entry.get("a")
    return None

def local_route_message(message: str) -> str:
    """
    Very simple rule-based router used as fallback when ADK route is missing or ambiguous.
    Returns agent name string.
    """
    m = (message or "").lower()
    if any(tok in m for tok in ("orientation", "how can i start", "how to start", "get started", "enroll", "onboard")):
        return "OrientationAgent"
    if any(tok in m for tok in ("access code", "access codes", "accesscode", "i need code", "code", "password", "login", "log in","can't log","cant log","lockdown")):
        return "TechSupportAgent"
    if any(tok in m for tok in ("progress", "where am i", "percent", "completion", "completed", "grade")):
        return "ProgressAgent"
    if any(tok in m for tok in ("refund", "refund policy", "class time", "timings", "schedule", "fees", "certificate", "how long", "duration")):
        return "FAQAgent"
    if any(tok in m for tok in ("traceback", "exception", "crash", "error", "server")):
        # return the canonical ErrorAgent name
        return "ErrorAgent"
    # default fallback
    return "FAQAgent"

# -------------------------
# HTML template (kept compact but same UI)
# -------------------------
HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Agents Intensive - Capstone Project</title>
  <style>
    body { font-family: Inter, Arial, sans-serif; background:#f4f6fb; margin:0; padding:18px; }
    .wrap { max-width:980px; margin:0 auto; }
    .card { background:white; border-radius:12px; padding:18px; box-shadow:0 10px 30px rgba(11,30,66,0.06); }
    h1 { margin:0 0 12px; font-size:26px; }
    .top { display:flex; gap:12px; align-items:center; margin-bottom:12px; }
    .username { padding:8px; border-radius:8px; border:1px solid #e6eefc; width:220px; }
    .btn { padding:8px 12px; border-radius:8px; border:none; background:#0b6ef6; color:white; cursor:pointer; }
    .btn.ghost { background:transparent; color:#0b6ef6; border:1px dashed #0b6ef6; }
    .chat { border-radius:10px; overflow:auto; height:520px; background:#fbfcff; padding:14px; border:1px solid #eef2ff; }
    .row { display:flex; gap:12px; }
    .left { flex:1 1 0; }
    .right { width:320px; }
    .msg { display:flex; margin:10px 0; align-items:flex-end; }
    .bubble { padding:10px 12px; border-radius:12px; max-width:78%; line-height:1.4; box-shadow:0 4px 14px rgba(11,30,66,0.04); }
    .user { justify-content:flex-end; }
    .user .bubble { background:#0b6ef6; color:white; border-bottom-right-radius:4px; }
    .agent { justify-content:flex-start; }
    .agent .avatar { width:40px; height:40px; display:flex; align-items:center; justify-content:center; border-radius:50%; margin-right:8px; font-size:18px; }
    .agent .bubble { background:#eef2ff; color:#08224a; border-bottom-left-radius:4px; }
    .meta { font-size:12px; color:#666; margin-top:6px; }
    .controls { display:flex; gap:8px; margin-top:12px; }
    input[type="text"].message { flex:1; padding:10px; border-radius:8px; border:1px solid #e6eefc; }
    .spinner { display:inline-block; width:18px; height:18px; border-radius:50%; border:3px solid #dfe7ff; border-top-color:#0b6ef6; animation:spin 1s linear infinite; margin-left:10px; vertical-align:middle; }
    @keyframes spin { to { transform:rotate(360deg); } }
    .log { margin-top:12px; padding:10px; background:#0b1220; color:#dbeafe; font-family:monospace; border-radius:8px; max-height:180px; overflow:auto; }
    .feature { display:inline-block; padding:4px 8px; background:#eef2ff; color:#042a6b; border-radius:999px; font-size:12px; margin-right:6px; margin-top:6px; }
    .agent-badge { font-size:12px; color:#fff; padding:4px 8px; border-radius:999px; margin-right:8px; background:#0b6ef6; }
    .status { font-size:13px; color:#334155; padding:6px 8px; border-radius:8px; background:#f1f5f9; display:inline-block; margin-left:8px; }
    .download { margin-top:8px; display:inline-block; padding:6px 8px; border-radius:8px; border:1px solid #e2e8f0; background:white; cursor:pointer; }
    /* agents list */
    .agents-panel { background:#fff; padding:10px; border-radius:8px; border:1px solid #eef2ff; margin-top:8px; }
    .agent-row { display:flex; align-items:center; gap:10px; padding:6px 4px; border-radius:6px; margin-bottom:6px; }
    .agent-row .avatar { width:36px; height:36px; font-size:16px; display:flex; align-items:center; justify-content:center; border-radius:50%; background:#eef2ff; color:#042a6b; }
    .agent-meta { flex:1; font-size:13px; color:#0b2540; }
    .agent-role { font-size:12px; color:#475569; }
    .status-dot { width:10px; height:10px; border-radius:50%; display:inline-block; margin-right:6px; vertical-align:middle; }
    .status-active { background:#16a34a; }
    .status-idle { background:#9ca3af; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Student Support Agent </h1>
      <div class="top">
        <input id="username" class="username" placeholder="username (e.g. bob)" value="bob" />
        <button id="startBtn" class="btn">Start Session</button>
        <div style="flex:1"></div>
        <div id="geminiStatus" class="status">Gemini: checking...</div>
      </div>

      <div class="row">
        <div class="left">
          <div id="chat" class="chat" aria-live="polite"></div>

          <div class="controls">
            <input id="message" class="message" type="text" placeholder="Ask something (try: 'How do I take exam?')" />
            <button id="askBtn" class="btn">Ask</button>
            <button id="clearBtn" class="btn ghost">Clear</button>
            <div id="spinner" style="display:none"><span class="spinner"></span></div>
          </div>

          <div id="log" class="log" style="margin-top:12px;"></div>
        </div>

        <div class="right">
          <div style="font-size:13px; color:#334155; margin-bottom:8px;">Session Info</div>
          <div id="sessionInfo" style="background:#fff;padding:10px;border-radius:8px;border:1px solid #eef2ff;font-family:monospace;">(no session)</div>

          <div style="margin-top:12px;">
            <div style="font-size:13px; color:#334155;">Agents (status)</div>

            <!-- Agents list goes here -->
            <div id="agentsList" class="agents-panel">
              <!-- Filled by JS -->
              <div style="font-size:13px;color:#64748b">Loading agents...</div>
            </div>

            <div style="margin-top:12px;">
              <div style="font-size:13px; color:#334155;">Download / Export</div>
              <button id="exportBtn" class="download">Download Session JSON</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
const chatEl = document.getElementById('chat');
const logEl = document.getElementById('log');
const sessionInfo = document.getElementById('sessionInfo');
const geminiStatusEl = document.getElementById('geminiStatus');
const spinner = document.getElementById('spinner');
const agentsListEl = document.getElementById('agentsList');

let SID = localStorage.getItem('sid') || null;

function appendAgentMessage(agentName, text, features) {
  const container = document.createElement('div');
  container.className = 'msg agent';
  const avatarSpan = document.createElement('div');
  avatarSpan.className = 'avatar';
  const avatar = ({"OrientationAgent":"🎓","TechSupportAgent":"🛠️","ProgressAgent":"📈","FAQAgent":"❓","Assistant":"🤖","ErrorAgent":"⚠️"})[agentName] || '🤖';
  avatarSpan.innerHTML = avatar;
  avatarSpan.style.background = '#eef2ff';
  avatarSpan.style.color = '#042a6b';
  avatarSpan.style.width = '40px';
  avatarSpan.style.height = '40px';
  avatarSpan.style.display = 'flex';
  avatarSpan.style.alignItems = 'center';
  avatarSpan.style.justifyContent = 'center';
  avatarSpan.style.borderRadius = '50%';
  avatarSpan.style.marginRight = '8px';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  let heading = `<div style="font-weight:600;margin-bottom:6px"><span class="agent-badge">${agentName}</span></div>`;
  bubble.innerHTML = heading + text.replace(/\\n/g,'<br/>');

  container.appendChild(avatarSpan);
  container.appendChild(bubble);

  // features badges
  if (features && Array.isArray(features)) {
    const fdiv = document.createElement('div');
    features.forEach(f => {
      const sp = document.createElement('span');
      sp.className = 'feature';
      sp.innerText = f;
      fdiv.appendChild(sp);
    });
    container.appendChild(fdiv);
  }

  chatEl.appendChild(container);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function appendUserMessage(text) {
  const container = document.createElement('div');
  container.className = 'msg user';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerText = text;
  container.appendChild(bubble);
  chatEl.appendChild(container);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function log(message) {
  const t = new Date().toISOString().substring(11,19);
  logEl.innerText = `${t} ${message}\\n` + logEl.innerText;
}

function showSpinner(show) {
  spinner.style.display = show ? 'inline-block' : 'none';
}

// render agents list
function renderAgents(agents) {
  agentsListEl.innerHTML = '';
  if (!agents || agents.length === 0) {
    agentsListEl.innerHTML = '<div style="font-size:13px;color:#64748b">No agents found</div>';
    return;
  }
  agents.forEach(a => {
    const row = document.createElement('div');
    row.className = 'agent-row';
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.innerText = a.emoji || '🤖';
    const meta = document.createElement('div');
    meta.className = 'agent-meta';
    meta.innerHTML = `<div style="font-weight:600">${a.name}</div><div class="agent-role">${a.role || ''}</div>`;
    const statusWrap = document.createElement('div');
    statusWrap.style.textAlign = 'right';
    const dot = document.createElement('span');
    dot.className = 'status-dot ' + (a.active ? 'status-active' : 'status-idle');
    const statusText = document.createElement('div');
    statusText.style.fontSize = '12px';
    statusText.style.color = a.active ? '#065f46' : '#475569';
    statusText.innerText = a.active ? 'Active' : 'Idle';
    statusWrap.appendChild(dot);
    statusWrap.appendChild(statusText);

    row.appendChild(avatar);
    row.appendChild(meta);
    row.appendChild(statusWrap);
    agentsListEl.appendChild(row);
  });
}

// fetch agents status
async function fetchAgentsStatus() {
  try {
    const r = await fetch('/agents_status');
    if (!r.ok) return;
    const j = await r.json();
    if (j.ok) {
      renderAgents(j.agents || []);
      if (typeof j.gemini_available !== 'undefined') {
        geminiStatusEl.innerText = 'Gemini: ' + (j.gemini_available ? 'Active' : 'Not active');
        geminiStatusEl.style.background = j.gemini_available ? '#ecfeff' : '#fff1f2';
        geminiStatusEl.style.color = j.gemini_available ? '#065f46' : '#831843';
      }
    }
  } catch(e) {
    console.error('agents_status error', e);
  }
}

let agentsPollHandle = null;
function startAgentsPolling() {
  fetchAgentsStatus();
  if (agentsPollHandle) clearInterval(agentsPollHandle);
  agentsPollHandle = setInterval(fetchAgentsStatus, 5000);
}

// start session
document.getElementById('startBtn').addEventListener('click', async () => {
  const username = document.getElementById('username').value.trim();
  if (!username) { alert('Enter username'); return; }
  try {
    const res = await fetch('/start_session', {
      method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({username})
    });
    const j = await res.json();
    if (!j.ok) { log('start_session error: ' + (j.error||'unknown')); return; }
    SID = j.sid;
    localStorage.setItem('sid', SID);
    sessionInfo.innerText = `sid=${SID}\\nuser=${username}\\nmessages=${(j.history||[]).length}`;
    chatEl.innerHTML = '';
    (j.history||[]).forEach(h => {
      if (h.role === 'user') appendUserMessage(h.text);
      else appendAgentMessage('Assistant', h.text, []);
    });
    log('session started: ' + SID);
  } catch(e) {
    log('start_session exception: ' + e.message);
  }
});

// ask
document.getElementById('askBtn').addEventListener('click', async () => {
  const txt = document.getElementById('message').value.trim();
  if (!txt) return;
  if (!SID) { log('No session. Start one.'); return; }
  appendUserMessage(txt);
  document.getElementById('message').value = '';
  showSpinner(true);
  try {
    const res = await fetch('/ask', {
      method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({sid:SID, message:txt})
    });
    const j = await res.json();
    showSpinner(false);
    if (j.ok) {
      appendAgentMessage(j.agent || 'Assistant', j.reply || '(no reply)', j.features || []);
      sessionInfo.innerText = `sid=${SID}\\nmessages=${j.messages_count||0}`;
      log('ask ok: ' + (j.agent || 'Assistant'));
    } else {
      appendAgentMessage('ErrorAgent', 'Server returned error: ' + (j.error||'unknown'), []);
      log('ask error: ' + (j.error||'unknown'));
    }
  } catch(e) {
    showSpinner(false);
    appendAgentMessage('ErrorAgent', 'Network or server error', []);
    log('ask exception: ' + e.message);
  }
});

// clear chat
document.getElementById('clearBtn').addEventListener('click', () => {
  chatEl.innerHTML = '';
  log('chat cleared');
});

// export session
document.getElementById('exportBtn').addEventListener('click', async () => {
  if (!SID) { alert('Start a session first'); return; }
  const res = await fetch('/export_session?sid=' + encodeURIComponent(SID));
  if (!res.ok) { log('export failed'); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `session_${SID}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  log('session exported');
});

// restore on load
window.addEventListener('load', async () => {
  try {
    const r = await fetch('/gemini_status');
    if (r.ok) {
      const j = await r.json();
      geminiStatusEl.innerText = 'Gemini: ' + (j.gemini_available ? 'Active' : 'Not active');
      geminiStatusEl.style.background = j.gemini_available ? '#ecfeff' : '#fff1f2';
      geminiStatusEl.style.color = j.gemini_available ? '#065f46' : '#831843';
    }
  } catch(e) { console.error(e); }

  startAgentsPolling();

  if (SID) {
    try {
      const r = await fetch('/session_info?sid=' + encodeURIComponent(SID));
      const j = await r.json();
      if (j.ok) {
        sessionInfo.innerText = `sid=${SID}\\nuser=${j.username||'(unknown)'}\\nmessages=${j.messages||0}`;
        chatEl.innerHTML = '';
        (j.history||[]).forEach(h => {
          if (h.role === 'user') appendUserMessage(h.text);
          else appendAgentMessage('Assistant', h.text, []);
        });
        log('restored session ' + SID);
      } else {
        log('no session to restore');
      }
    } catch(e) {
      log('session_info error: ' + e.message);
    }
  } else {
    log('no session in storage');
  }
});
</script>
</body>
</html>
"""

# -------------------------
# API endpoints (ADK routing)
# -------------------------

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/gemini_status")
def gemini_status():
    try:
        sdk_loaded = False
        api_key_found = False
        gemini_available = is_gemini_available()
        try:
            importlib.import_module("google.genai")
            sdk_loaded = True
        except Exception:
            sdk_loaded = False
        api_key_found = bool(os.environ.get("GEMINI_API_KEY"))
        return jsonify({"sdk_loaded": sdk_loaded, "api_key_found": api_key_found, "gemini_available": gemini_available})
    except Exception as e:
        logging.exception("gemini_status failed")
        return jsonify({"error": str(e)}), 500

@app.route("/agents_status")
def agents_status():
    try:
        agents = []
        gemini_available = is_gemini_available()
        subs = {}
        try:
            subs = getattr(root_agent, "subagents", {}) or getattr(root_agent, "sub", {}) or {}
        except Exception:
            subs = {}

        # helper to detect availability flexibly
        def _detect_active(agent_obj):
            if agent_obj is None:
                return False, "missing"
            # 1. common attribute .llm.available
            try:
                llm = getattr(agent_obj, "llm", None)
                if llm is not None:
                    avail = getattr(llm, "available", None)
                    if isinstance(avail, bool):
                        return bool(avail), "llm.available"
                    # maybe method
                    if callable(getattr(llm, "is_available", None)):
                        try:
                            return bool(llm.is_available()), "llm.is_available()"
                        except Exception:
                            pass
            except Exception:
                logging.exception("checking llm.available failed")

            # 2. direct attributes like .active, .available, .online, .status
            for attr in ("active", "available", "online"):
                try:
                    v = getattr(agent_obj, attr, None)
                    if isinstance(v, bool):
                        return bool(v), attr
                    if isinstance(v, str) and v.lower() in ("running","online","active"):
                        return True, attr
                except Exception:
                    logging.exception("checking attr %s failed", attr)

            # 3. methods like is_available(), ping(), health()
            for meth in ("is_available", "available", "ping", "health_check", "health"):
                try:
                    fn = getattr(agent_obj, meth, None)
                    if callable(fn):
                        try:
                            res = fn()
                        except TypeError:
                            res = None
                        # interpret result broadly
                        if isinstance(res, bool):
                            return bool(res), meth + "()"
                        if isinstance(res, dict) and res.get("ok") is True:
                            return True, meth + "()"
                        if isinstance(res, str) and res.lower() in ("ok","available","healthy","alive"):
                            return True, meth + "()"
                except Exception:
                    logging.exception("calling method %s on agent failed", meth)

            # 4. fallback: agent object exists but no clear flag
            return False, "unknown"

        for name, meta in AGENT_AVATARS.items():
            agent_obj = subs.get(name)
            active = False
            reason = "not_checked"
            try:
                is_active, reason = _detect_active(agent_obj)
                active = bool(is_active)
            except Exception:
                logging.exception("checking agent active state failed for %s", name)
                active = False
                reason = "exception"

            agents.append({
                "name": name,
                "emoji": meta.get("emoji"),
                "role": meta.get("label"),
                "active": active,
                "reason": reason
            })

        return jsonify({"ok": True, "agents": agents, "gemini_available": gemini_available})
    except Exception as e:
        logging.exception("agents_status failed")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/start_session", methods=["POST"])
def start_session():
    try:
        data = request.get_json() or {}
        username = data.get("username","").strip()
        if not username:
            return jsonify({"ok": False, "error": "username required"}), 400
        sid = memory.create_session(username)
        s = memory.get_session(sid) or {}
        history = s.get("history", [])
        return jsonify({"ok": True, "sid": sid, "history": history})
    except Exception as e:
        logging.exception("start_session failed")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/ask", methods=["POST"])
def ask():
    try:
        data = request.get_json() or {}
        sid = data.get("sid")
        message = data.get("message","")
        if not sid:
            return jsonify({"ok": False, "error": "sid required"}), 400
        if not message:
            return jsonify({"ok": False, "error": "message required"}), 400
        if root_agent is None:
            return jsonify({"ok": False, "error": "root agent not initialized"}), 500

        logging.info("web_demo ask sid=%s message=%s", sid, message[:120])

        # Call root_agent.route and parse its result
        try:
            route_result = root_agent.route(sid, message)
        except Exception:
            logging.exception("root_agent.route call failed")
            route_result = None

        # --- start replacement block (improved agent routing) ---
        logging.info("ROUTE RESULT: %s", repr(route_result))

        agent_name = "Assistant"
        reply_text = ""

        # Helper: try to extract agent-like keys from dict
        def _extract_agent_from_dict(d):
            for k in ("agent", "from", "source", "subagent", "handler"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v
            return None

        # 1) Parse route_result robustly
        try:
            if isinstance(route_result, dict):
                agent_name_candidate = _extract_agent_from_dict(route_result)
                if agent_name_candidate:
                    agent_name = agent_name_candidate
                # multiple possible text fields
                for k in ("content", "reply", "text", "message", "output", "response"):
                    v = route_result.get(k)
                    if isinstance(v, str) and v.strip():
                        reply_text = v.strip()
                        break
                # if still empty, maybe there's a nested 'result' or 'items'
                if not reply_text:
                    for k in ("result", "results", "items"):
                        v = route_result.get(k)
                        if isinstance(v, str) and v.strip():
                            reply_text = v.strip()
                            break
            elif isinstance(route_result, (list, tuple)) and route_result:
                # common ADK pattern: [agent_name, reply_text, ...]
                if len(route_result) >= 2 and isinstance(route_result[0], str) and isinstance(route_result[1], str):
                    agent_name = route_result[0] or agent_name
                    reply_text = route_result[1]
                else:
                    # join parts as last resort
                    reply_text = " ".join([str(x) for x in route_result if x])
            elif isinstance(route_result, str):
                reply_text = route_result.strip()
            else:
                # unknown shape; stringify
                reply_text = str(route_result or "").strip()
        except Exception:
            logging.exception("Error parsing route_result")

        # 2) Heuristic: if root_agent returned a plain/generic assistant reply (no agent metadata),
        # prefer local routing / KB answer for short or non-specific responses.
        def _looks_generic_assistant(text: str) -> bool:
            if not text:
                return True
            t = text.lower()
            # heuristics: generic framing phrases or short responses
            generic_phrases = ("i can", "here are", "sure", "happy to", "i'm here to", "i can help", "please provide", "you can")
            if any(p in t for p in generic_phrases):
                return True
            if len(text.split()) <= 20 and len(text) > 0:
                # short replies might be either specific or generic — be cautious: only treat as generic if no agent metadata
                return True
            return False

        # If route_result didn't provide an agent (kept default Assistant) and the reply looks generic,
        # ask local router to pick a specialized agent and try local KB before returning the generic answer.
        used_local_kb = False
        if (agent_name in (None, "", "Assistant")):
            if reply_text and not reply_text.isspace():
                if _looks_generic_assistant(reply_text):
                    chosen = local_route_message(message)
                    logging.info("ROOT returned generic reply; using local router -> %s", chosen)
                    agent_name = chosen
                    kb_answer = best_kb_match(agent_name, message)
                    if kb_answer:
                        reply_text = kb_answer
                        used_local_kb = True
            else:
                # no reply_text at all: pick an agent by local router immediately
                chosen = local_route_message(message)
                logging.info("No reply_text from root -> local router -> %s", chosen)
                agent_name = chosen
                kb_answer = best_kb_match(agent_name, message)
                if kb_answer:
                    reply_text = kb_answer
                    used_local_kb = True

        # 3) If still no reply_text, try local KB once more (for any chosen agent)
        if not reply_text:
            try:
                kb_answer = best_kb_match(agent_name, message)
                if kb_answer:
                    reply_text = kb_answer
                    used_local_kb = True
            except Exception:
                logging.exception("best_kb_match failed")

        # 4) Final fallback
        if not reply_text:
            reply_text = "Sorry — I couldn't find a direct answer. Please provide more details (e.g., username, course code)."

        # record message to memory
        feat = features_for_message(agent_name, message)
        try:
            if hasattr(memory, "add_message"):
                memory.add_message(sid, "user", message)
                memory.add_message(sid, "assistant", reply_text)
        except Exception:
            logging.exception("memory add_message failed")

        s = memory.get_session(sid) or {}
        messages_count = len(s.get("history", []))

        # add a small hint when we used local KB instead of root agent to help debugging
        if used_local_kb:
            # prefix a short trace so you can see the fallback happened in the UI
            reply_text = "(LocalKB answer)\n" + reply_text

        return jsonify({"ok": True, "reply": reply_text, "agent": agent_name, "features": feat.get("features",[]), "messages_count": messages_count})
        # --- end replacement block ---
    except Exception as e:
        logging.exception("ask error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/session_info")
def session_info():
    sid = request.args.get("sid")
    if not sid:
        return jsonify({"ok": False, "error": "sid required"}), 400
    s = memory.get_session(sid)
    if not s:
        return jsonify({"ok": False, "error": "unknown sid"}), 404
    username = s.get("username")
    history = s.get("history", [])
    return jsonify({"ok": True, "username": username, "history": history, "messages": len(history)})

@app.route("/export_session")
def export_session():
    sid = request.args.get("sid")
    if not sid:
        return jsonify({"ok": False, "error": "sid required"}), 400
    s = memory.get_session(sid)
    if not s:
        return jsonify({"ok": False, "error": "unknown sid"}), 404
    data = json.dumps(s, indent=2)
    return send_file(io.BytesIO(data.encode("utf-8")), mimetype="application/json", as_attachment=True, download_name=f"session_{sid}.json")

if __name__ == "__main__":
    app.run(debug=True)
