"""OTel run projection for the dashboard Flow view.

Claude Code emits native OpenTelemetry (`CLAUDE_CODE_ENABLE_TELEMETRY`) → OTLP → a local
Jaeger store. This module is the *thin* read layer the dashboard's Flow view consumes: it
queries Jaeger's HTTP API and projects each trace into a run summary + a span graph, with
the cost/token/tool/agent insight Jaeger's own UI does not surface. Jaeger is the invisible
store; the dashboard is the face. No custom telemetry, no internal-file parsing — the
documented OTel export is the source.

Pure-ish: `summarize_trace`/`build_graph` are pure over a Jaeger trace dict (unit-testable);
`fetch_runs`/`fetch_run` add the Jaeger HTTP call (best-effort — a down Jaeger yields []).
"""
import json
import urllib.request
import urllib.parse

JAEGER_BASE = "http://localhost:16686"
SERVICE = "claude-code"

# Approx USD per 1M tokens (input, output). Edit as pricing moves; cost is a derived estimate,
# never authoritative (no OTel attribute defines cost — it is usage × price).
PRICES = {
    "claude-opus-5": (15.0, 75.0), "claude-opus-4-8": (15.0, 75.0), "claude-opus-4-1": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0), "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.8, 4.0), "claude-3-5-haiku": (0.8, 4.0),
}
DEFAULT_PRICE = (5.0, 15.0)


def _tag(span, key, default=None):
    for t in span.get("tags", []) or []:
        if t.get("key") == key:
            return t.get("value")
    return default


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _cost(model, tin, tout, tcw, tcr):
    pin, pout = PRICES.get(model, DEFAULT_PRICE)
    # cache write ≈ 1.25× input, cache read ≈ 0.1× input (Anthropic prompt-cache pricing)
    return (tin * pin + tout * pout + tcw * pin * 1.25 + tcr * pin * 0.1) / 1_000_000.0


def _spans_of_type(spans, ty):
    return [s for s in spans if _tag(s, "span.type") == ty]


def _root(spans):
    for s in spans:
        if not s.get("references"):
            return s
    return spans[0] if spans else None


def summarize_trace(trace):
    """A Jaeger trace dict → one run summary row (pure)."""
    spans = trace.get("spans", []) or []
    if not spans:
        return None
    interactions = _spans_of_type(spans, "interaction")
    if not interactions:
        return None      # partial / in-progress trace (interaction span not exported yet) — skip it
    inter = interactions[0]
    root = _root(spans) or inter
    llm = _spans_of_type(spans, "llm_request")
    tin = sum(_num(_tag(s, "input_tokens")) for s in llm)
    tout = sum(_num(_tag(s, "output_tokens")) for s in llm)
    tcw = sum(_num(_tag(s, "cache_creation_tokens")) for s in llm)
    tcr = sum(_num(_tag(s, "cache_read_tokens")) for s in llm)
    model = next((_tag(s, "model") for s in llm if _tag(s, "model")), "—")
    tools = [(_tag(s, "tool_name") or "?", _tag(s, "agent_id")) for s in _spans_of_type(spans, "tool")]
    execs = _spans_of_type(spans, "tool.execution")
    errors = sum(1 for s in execs if str(_tag(s, "success")).lower() not in ("true", "1"))
    start_us = min((s.get("startTime", 0) for s in spans), default=0)
    dur_ms = _num(_tag(inter, "interaction.duration_ms"))
    if not dur_ms:
        end_us = max((s.get("startTime", 0) + s.get("duration", 0) for s in spans), default=start_us)
        dur_ms = (end_us - start_us) / 1000.0
    agents = sorted({a for _, a in tools if a})
    tool_names = {}
    for name, _ in tools:
        tool_names[name] = tool_names.get(name, 0) + 1
    top_tools = sorted(tool_names.items(), key=lambda kv: -kv[1])
    return {
        "trace_id": trace.get("traceID"),
        "title": inter.get("operationName", "run"),   # always the interaction span's op → a clean title
        "prompt_len": int(_num(_tag(inter, "user_prompt_length"))),
        "session_id": _tag(inter, "session.id"),
        "model": model,
        "started": start_us / 1_000_000.0,
        "duration_ms": int(round(dur_ms)),
        "in_tokens": int(tin), "out_tokens": int(tout),
        "cache_w": int(tcw), "cache_r": int(tcr),
        "total_tokens": int(tin + tout + tcw + tcr),
        "cost_usd": round(_cost(model, tin, tout, tcw, tcr), 4),
        "span_count": len(spans), "tool_count": len(tools), "agent_count": len(agents),
        "top_tools": [{"name": n, "n": c} for n, c in top_tools[:6]],
        "errors": errors,
        "status": "error" if errors else "ok",
    }


def build_graph(trace):
    """A Jaeger trace dict → {nodes, edges, summary} for the Flow view's graph (pure).
    Each span is a node; edges come from CHILD_OF references (parent_span_id). Depth is the
    hop count from the root, so the view can rank nodes left→right without a layout engine."""
    spans = trace.get("spans", []) or []
    by_id = {s["spanID"]: s for s in spans}
    parent = {}
    for s in spans:
        p = None
        for ref in s.get("references", []) or []:
            if ref.get("refType") == "CHILD_OF":
                p = ref.get("spanID"); break
        parent[s["spanID"]] = p

    def depth(sid, seen=()):
        p = parent.get(sid)
        if not p or p in seen or p not in by_id:
            return 0
        return 1 + depth(p, seen + (sid,))

    nodes = []
    for s in spans:
        sid = s["spanID"]
        ty = _tag(s, "span.type") or ""
        tool = _tag(s, "tool_name")
        model = _tag(s, "model")
        label = tool or model or (s.get("operationName", "").replace("claude_code.", ""))
        tin = _num(_tag(s, "input_tokens")); tout = _num(_tag(s, "output_tokens"))
        ok = str(_tag(s, "success", "true")).lower() in ("true", "1")
        nodes.append({
            "id": sid, "label": label, "type": ty,
            "op": s.get("operationName", ""), "depth": depth(sid),
            "dur_ms": int(round(s.get("duration", 0) / 1000.0)),
            "tokens": int(tin + tout), "model": model, "tool": tool,
            "agent": _tag(s, "agent_id"), "ok": ok,
        })
    edges = [{"source": parent[s["spanID"]], "target": s["spanID"]}
             for s in spans if parent.get(s["spanID"]) in by_id]
    return {"nodes": nodes, "edges": edges, "summary": summarize_trace(trace)}


# ── Jaeger HTTP (best-effort; a down/absent Jaeger degrades to empty, never raises) ──

def _get(path):
    try:
        with urllib.request.urlopen(JAEGER_BASE + path, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def fetch_runs(lookback="3h", limit=60):
    q = urllib.parse.urlencode({"service": SERVICE, "lookback": lookback, "limit": limit})
    data = _get("/api/traces?" + q)
    traces = (data or {}).get("data") or []
    runs = [r for r in (summarize_trace(t) for t in traces) if r]
    runs.sort(key=lambda r: -(r.get("started") or 0))
    # aggregate strip
    agg = {
        "runs": len(runs),
        "tokens": sum(r["total_tokens"] for r in runs),
        "cost_usd": round(sum(r["cost_usd"] for r in runs), 2),
        "errors": sum(r["errors"] for r in runs),
        "jaeger_up": data is not None,
    }
    return {"runs": runs, "agg": agg}


def fetch_run(trace_id):
    data = _get("/api/traces/" + urllib.parse.quote(trace_id, safe=""))
    traces = (data or {}).get("data") or []
    if not traces:
        return {"nodes": [], "edges": [], "summary": None}
    return build_graph(traces[0])
