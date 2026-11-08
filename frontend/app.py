"""CodeIntel dashboard (Streamlit). Talks to the FastAPI backend only.
Run from the project root:  streamlit run frontend/app.py"""
import html
import json
import os
import re
import time

import pandas as pd
import requests
import streamlit as st

API = os.environ.get("CODEINTEL_API_URL", "http://localhost:8000").rstrip("/")
KEY = os.environ.get("CODEINTEL_API_KEY", "")
HEADERS = {"X-API-Key": KEY} if KEY else {}
SEVERITIES = ["critical", "high", "medium", "low", "info"]
MAX_SHOWN = 100

st.set_page_config(page_title="CodeIntel", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {padding-top: 2rem; max-width: 1280px;}
h1 {font-weight: 700; letter-spacing: -0.02em; color: #0F172A;}
[data-testid="stSidebar"] {border-right: 1px solid #E2E8F0;}
[data-testid="stMetric"] {background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 14px 16px;}
[data-testid="stMetricLabel"] {color: #475569;}
[data-testid="stExpander"] {border: 1px solid #E2E8F0; border-radius: 10px;}
.pill {display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .72rem; font-weight: 600;
       letter-spacing: .04em; text-transform: uppercase; margin-right: 6px;}
.sev-critical {background: #FECACA; color: #7F1D1D;} .sev-high {background: #FEE2E2; color: #B91C1C;}
.sev-medium {background: #FEF3C7; color: #92400E;}   .sev-low {background: #DBEAFE; color: #1E40AF;}
.sev-info {background: #E2E8F0; color: #334155;}
.src-confirmed {border: 1px solid #15803D; color: #15803D;} .src-rule {border: 1px solid #64748B; color: #475569;}
.src-ai {border: 1px solid #6D28D9; color: #6D28D9;}
.dot {display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px;}
.dot-ok {background: #16A34A;} .dot-off {background: #94A3B8;}
</style>""", unsafe_allow_html=True)


def pill(text: str, cls: str) -> str:
    return f'<span class="pill {cls}">{html.escape(text)}</span>'      # repo-derived text is always escaped


def provenance(f: dict) -> tuple[str, str]:
    if f["confirmed"]:
        return "Tool confirmed", "src-confirmed"
    return ("AI suggestion", "src-ai") if f["source"] == "llm" else ("Rule-based", "src-rule")


def api(method: str, path: str, **kw):
    try:
        r = requests.request(method, f"{API}{path}", headers=HEADERS, timeout=kw.pop("timeout", 30), **kw)
    except requests.RequestException as e:
        st.error(f"Cannot reach the API at {API}. Is it running? ({type(e).__name__})")
        st.stop()
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        st.error(f"Request failed ({r.status_code}): {detail}")
        return None
    return r.json() if r.content else {}


def wait_repo(rid: str):
    with st.spinner("Indexing repository: fetching, parsing, embedding..."):
        for _ in range(600):
            r = api("GET", f"/repositories/{rid}")
            if r and r["status"] in ("ready", "failed"):
                return r
            time.sleep(1)


def to_markdown(rep: dict) -> str:
    out = ["# CodeIntel report", "", rep["summary"], ""]
    for f in rep["findings"]:
        out += [f"## [{f['severity'].upper()}] {f['title']}", f"`{f['file']}:{f['line_start']}` - {provenance(f)[0]}", "",
                f["description"], ""]
        if f["evidence"]:
            out += ["```", f["evidence"], "```", ""]
        if f["suggestion"]:
            out += [f"**Fix:** {f['suggestion']}", ""]
    return "\n".join(out)


# ------------------------------------------------------------------ sidebar
health = api("GET", "/health") or {}
sb = st.sidebar
sb.markdown("### CodeIntel")
sb.caption("Code intelligence platform")
ai_on = health.get("llm_available")
sb.markdown(f'<span class="dot dot-ok"></span>Backend connected (v{html.escape(str(health.get("version", "?")))})<br>'
            f'<span class="dot {"dot-ok" if ai_on else "dot-off"}"></span>'
            + (f"AI model: {html.escape(health.get('llm_model', ''))}" if ai_on else "AI model not connected (rule-based mode)"),
            unsafe_allow_html=True)
sb.divider()


def after_add(r):
    if not r:
        return
    res = wait_repo(r["id"])
    if res and res["status"] == "ready":
        st.session_state["repo_id"] = r["id"]
        st.rerun()
    elif res:
        st.error(res["error"] or "Indexing failed")


with sb.expander("Add a repository", expanded=not st.session_state.get("repo_id")):
    mode = st.radio("Source", ["Git URL", "ZIP upload"], horizontal=True, label_visibility="collapsed")
    if mode == "Git URL":
        url = st.text_input("HTTPS repository URL", placeholder="https://github.com/owner/project")
        if st.button("Clone and index", disabled=not url, key="add_git", width="stretch"):
            after_add(api("POST", "/repositories", json={"git_url": url}))
    else:
        zf = st.file_uploader("Project archive", type=["zip"])
        if st.button("Upload and index", disabled=not zf, key="add_zip", width="stretch"):
            after_add(api("POST", "/repositories/upload", files={"file": (zf.name, zf.getvalue(), "application/zip")}, timeout=300))

repos = [r for r in (api("GET", "/repositories") or []) if r["status"] == "ready"]
if repos:
    labels = {r["id"]: f"{r['name']}  ({r['id'][:6]})" for r in repos}
    ids = list(labels)
    cur = st.session_state.get("repo_id")
    st.session_state["repo_id"] = sb.selectbox("Repository", ids, index=ids.index(cur) if cur in ids else 0,
                                               format_func=labels.get)
    with sb.expander("Manage"):
        sure = st.checkbox("I want to delete this repository and its index")
        if st.button("Delete repository", disabled=not sure, key="del_repo"):
            api("DELETE", f"/repositories/{st.session_state['repo_id']}")
            st.session_state.pop("repo_id", None)
            st.rerun()
rid = st.session_state.get("repo_id")

st.title("CodeIntel")
st.caption("Understand, inspect and evaluate an existing codebase, with evidence for every finding.")
if not rid:
    st.info("Add a repository from the sidebar to begin. Supported: public HTTPS git URLs and ZIP archives.")
    st.stop()

tab_over, tab_chat, tab_an, tab_files = st.tabs(["Overview", "Ask the repository", "Analysis", "Generated files"])

# ------------------------------------------------------------------ overview
with tab_over:
    cache = st.session_state.setdefault("overview", {})
    if rid not in cache:
        cache[rid] = api("GET", f"/repositories/{rid}/overview")
    ov = cache[rid]
    if ov:
        d = ov["data"]
        names = {"files": "Files", "code_files": "Code files", "classes": "Classes", "functions": "Functions",
                 "undocumented_public": "Undocumented", "tests": "Test files"}
        for col, (k, label) in zip(st.columns(len(names)), names.items()):
            col.metric(label, d["counts"].get(k, 0))
        left, right = st.columns([3, 2])
        with left:
            st.subheader("Languages (lines of code)")
            st.bar_chart(pd.DataFrame({"lines": d["languages"]}), height=220)
        with right:
            st.subheader("Detected")
            st.write("**Frameworks:** " + (", ".join(d["frameworks"]) or "none detected"))
            st.write("**Entry points:** " + (", ".join(f"`{e}`" for e in d["entry_points"]) or "none detected"))
            if d["central_files"]:
                st.write("**Most imported files:** " + ", ".join(f"`{c['file']}`" for c in d["central_files"][:5]))
        if d["endpoints"]:
            st.subheader("HTTP endpoints")
            st.dataframe(pd.DataFrame(d["endpoints"])[["method", "path", "handler", "file", "line"]],
                         width="stretch", hide_index=True)
        with st.expander("Architecture notes"):
            st.markdown(ov["artifacts"].get("architecture.md", ""))

# ------------------------------------------------------------------ chat
with tab_chat:
    st.caption("Examples: which files contain the classes? | how does login work? | where are errors handled?")
    history = st.session_state.setdefault("chat", {}).setdefault(rid, [])
    modes = {"index": "Answered from the code index", "llm": "AI answer", "retrieval_only": "Ranked search results"}
    for m in history:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("mode"):
                st.caption(modes.get(m["mode"], ""))
            for c in m.get("citations", [])[:5]:
                with st.expander(f"{c['file']}:{c['line_start']}-{c['line_end']}"):
                    st.code(c["snippet"])
    if q := st.chat_input("Ask a question about this repository"):
        history.append({"role": "user", "content": q})
        with st.spinner("Searching the codebase..."):
            r = api("POST", f"/repositories/{rid}/chat", json={"question": q}, timeout=300)
        if r:
            history.append({"role": "assistant", "content": r["answer"], "citations": r["citations"], "mode": r["mode"]})
        st.rerun()

# ------------------------------------------------------------------ analysis
with tab_an:
    task = st.text_area("Task", "Analyze this repository for bugs, security issues and missing test coverage.", height=80)
    picked = st.multiselect("Agents (leave empty to let the supervisor choose)", health.get("agents", []))
    if st.button("Run analysis", type="primary", key="run_analysis"):
        r = api("POST", f"/repositories/{rid}/analysis", json={"task": task, "agents": picked or None})
        if r:
            st.caption("Selected agents: " + ", ".join(r["agents"]))
            with st.spinner("Agents are working..."):
                while True:
                    a = api("GET", f"/analyses/{r['analysis_id']}")
                    if not a or a["status"] != "running":
                        break
                    time.sleep(1)
            if a and a["status"] == "done":
                st.session_state.setdefault("reports", {})[rid] = a["report"]
            elif a:
                st.error(a["error"])
    rep = st.session_state.get("reports", {}).get(rid)
    if rep:
        s = rep["stats"]
        for col, (label, val) in zip(st.columns(4), [("Findings", s["total_findings"]), ("Tool confirmed", s["tool_confirmed"]),
                                                     ("Rule-based", s["heuristic"]), ("AI suggestions", s["ai_hypotheses"])]):
            col.metric(label, val)
        with st.expander("Summary", expanded=True):
            st.text(rep["summary"])
        f1, f2, f3 = st.columns([2, 2, 3])
        sev = f1.multiselect("Severity", SEVERITIES, SEVERITIES[:4])
        agt = f2.multiselect("Agent", sorted(s["by_agent"]), sorted(s["by_agent"]))
        term = f3.text_input("Search", placeholder="file name or keyword").lower()
        shown = [f for f in rep["findings"] if f["severity"] in sev and f["agent"] in agt
                 and (not term or term in (f["title"] + f["file"] + f["description"]).lower())]
        st.caption(f"Showing {min(len(shown), MAX_SHOWN)} of {len(shown)} matching findings")
        for f in shown[:MAX_SHOWN]:
            label = re.sub(r"[\[\]()`*_]", "", f"{f['severity'].upper()}  |  {f['title']}  |  {f['file']}:{f['line_start']}")
            with st.expander(label[:180]):
                prov, cls = provenance(f)
                st.markdown(pill(f["severity"], f"sev-{f['severity']}") + pill(prov, cls) + pill(f"{f['confidence']:.0%} confidence", "src-rule"),
                            unsafe_allow_html=True)
                st.text(f["description"])
                if f["evidence"]:
                    st.code(f["evidence"], language="python")
                if f["suggestion"]:
                    st.markdown("**Suggested fix**")
                    st.text(f["suggestion"])
                for n in f["notes"]:
                    st.text("Note: " + n)
                st.caption(f"Source tool: {f['tool']}  |  Category: {f['category']}")
        notices = [w for w in rep["warnings"] if "semgrep" not in w.lower()]     # optional scanner, hidden in the UI
        if notices:
            with st.expander(f"Notices ({len(notices)})"):
                for w in notices:
                    st.text(w)
        d1, d2, _ = st.columns([1, 1, 4])
        d1.download_button("Download JSON", json.dumps(rep, indent=2), "codeintel-report.json", key="dl_json")
        d2.download_button("Download Markdown", to_markdown(rep), "codeintel-report.md", key="dl_md")

# ------------------------------------------------------------------ generated files
with tab_files:
    rep = st.session_state.get("reports", {}).get(rid)
    arts = [(a, n, b) for a, r in (rep or {}).get("results", {}).items() for n, b in r["artifacts"].items()]
    if not arts:
        st.info("Run an analysis that includes the testing or documentation agents to generate test templates and docs.")
    for agent, name, body in arts:
        with st.expander(f"{name}   ({agent})"):
            if name.endswith(".py"):
                st.code(body, language="python")
            else:
                st.markdown(body)
            st.download_button("Download", body, name.replace("/", "_"), key=f"dl_{agent}_{name}")