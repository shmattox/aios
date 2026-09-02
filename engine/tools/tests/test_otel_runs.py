import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
import otel_runs as o


def _span(sid, op, tags, parent=None, dur=1000, start=1_000_000):
    s = {"spanID": sid, "operationName": op, "startTime": start, "duration": dur,
         "tags": [{"key": k, "value": v} for k, v in tags.items()]}
    if parent:
        s["references"] = [{"refType": "CHILD_OF", "spanID": parent}]
    return s


TRACE = {"traceID": "abc123", "spans": [
    _span("s1", "claude_code.interaction",
          {"span.type": "interaction", "interaction.duration_ms": 5000,
           "session.id": "sess", "user_prompt_length": 42}, start=1_000_000),
    _span("s2", "claude_code.llm_request",
          {"span.type": "llm_request", "model": "claude-opus-5",
           "input_tokens": 100, "output_tokens": 50,
           "cache_creation_tokens": 200, "cache_read_tokens": 0}, parent="s1", start=1_000_500),
    _span("s3", "claude_code.tool",
          {"span.type": "tool", "tool_name": "Agent", "agent_id": "ag1"}, parent="s1", start=1_001_000),
    _span("s4", "claude_code.tool.execution",
          {"span.type": "tool.execution", "success": "false"}, parent="s3", start=1_001_200),
]}
PARTIAL = {"traceID": "xyz", "spans": [
    _span("t1", "claude_code.tool.blocked_on_user", {"span.type": "tool.blocked_on_user"})]}


def test_summarize_tokens_cost_agents_errors():
    r = o.summarize_trace(TRACE)
    assert r["model"] == "claude-opus-5"
    assert r["in_tokens"] == 100 and r["out_tokens"] == 50 and r["cache_w"] == 200
    assert r["total_tokens"] == 350
    assert r["tool_count"] == 1 and r["agent_count"] == 1          # one subagent (agent_id ag1)
    assert r["errors"] == 1 and r["status"] == "error"            # s4 success=false
    # cost = (100*5 + 50*25 + 200*5*1.25)/1e6 = 3000/1e6  (Opus 5 first-party rate, 2026-09-02)
    assert abs(r["cost_usd"] - 0.003) < 1e-6
    assert r["title"] == "claude_code.interaction"


def test_partial_trace_without_interaction_is_skipped():
    # a lone blocked_on_user span (no llm, no tool) is still a partial husk, not a run
    assert o.summarize_trace(PARTIAL) is None
    assert o.summarize_trace({"traceID": "e", "spans": []}) is None


def test_interactionless_trace_summarizes_from_spans():
    # Current Claude Code exports no `interaction` wrapper span — the summary derives from the
    # llm/tool spans: session.id from any span, duration from the span envelope, synthetic title.
    t = {"traceID": "modern1", "spans": [
        _span("l1", "claude_code.llm_request",
              {"span.type": "llm_request", "model": "claude-opus-5", "session.id": "sess-42",
               "input_tokens": 10, "output_tokens": 20,
               "cache_creation_tokens": 0, "cache_read_tokens": 5}, start=1_000_000, dur=2_000_000),
        _span("t1", "claude_code.tool",
              {"span.type": "tool", "tool_name": "Bash", "session.id": "sess-42"},
              parent="l1", start=1_500_000, dur=500_000),
    ]}
    r = o.summarize_trace(t)
    assert r is not None
    assert r["session_id"] == "sess-42"
    assert r["model"] == "claude-opus-5" and r["total_tokens"] == 35
    assert r["duration_ms"] == 2000                 # envelope: 1_000_000..3_000_000 µs
    assert r["title"] == "turn · 1 llm · 1 tool"
    assert r["prompt_len"] == 0 and r["status"] == "ok"


def test_build_graph_nodes_edges_and_depth():
    g = o.build_graph(TRACE)
    byid = {n["id"]: n for n in g["nodes"]}
    assert len(g["nodes"]) == 4 and len(g["edges"]) == 3
    assert byid["s1"]["depth"] == 0 and byid["s2"]["depth"] == 1 and byid["s4"]["depth"] == 2
    assert byid["s3"]["label"] == "Agent" and byid["s3"]["agent"] == "ag1"
    assert byid["s4"]["ok"] is False                              # failed execution surfaces in the graph


def test_cost_zero_and_unknown_model_falls_back():
    assert o.summarize_trace({"traceID": "z", "spans": [
        _span("i", "claude_code.interaction", {"span.type": "interaction"})]})["cost_usd"] == 0.0


def test_error_kinds_groups_by_parent_tool():
    r = o.summarize_trace(TRACE)
    assert r["error_kinds"] == {"Agent": 1}      # s4 exec failed; its CHILD_OF parent s3 is tool "Agent"
    assert r["priced"] is True                    # claude-opus-5 is in PRICES


def test_priced_flag_true_for_no_model_sentinel():
    # a run with no llm span → model "—" → NOT an unpriced real model
    r = o.summarize_trace({"traceID": "n", "spans": [
        _span("i", "claude_code.interaction", {"span.type": "interaction"})]})
    assert r["priced"] is True and r["error_kinds"] == {}


def test_priced_flag_false_for_unknown_real_model():
    t = {"traceID": "u", "spans": [
        _span("i", "claude_code.interaction", {"span.type": "interaction"}),
        _span("l", "claude_code.llm_request",
              {"span.type": "llm_request", "model": "claude-future-9",
               "input_tokens": 10, "output_tokens": 5}, parent="i")]}
    r = o.summarize_trace(t)
    assert r["priced"] is False and r["model"] == "claude-future-9"


def test_aggregate_merges_kinds_unpriced_and_percentiles():
    runs = [
        {"total_tokens": 100, "cost_usd": 0.01, "errors": 1, "duration_ms": 1000,
         "error_kinds": {"Agent": 1}, "model": "claude-opus-5", "priced": True},
        {"total_tokens": 200, "cost_usd": 0.02, "errors": 2, "duration_ms": 3000,
         "error_kinds": {"Agent": 1, "Bash": 2}, "model": "claude-future-9", "priced": False},
        {"total_tokens": 50, "cost_usd": 0.0, "errors": 0, "duration_ms": 2000,
         "error_kinds": {}, "model": "—", "priced": True},
    ]
    agg = o._aggregate(runs, jaeger_up=True)
    assert agg["error_kinds"] == {"Agent": 2, "Bash": 2}
    assert agg["unpriced_models"] == ["claude-future-9"]
    assert agg["p50_ms"] == 2000 and agg["p95_ms"] == 3000
    assert agg["runs"] == 3 and agg["errors"] == 3


def test_aggregate_empty_is_safe():
    agg = o._aggregate([], jaeger_up=False)
    assert agg["p50_ms"] is None and agg["p95_ms"] is None
    assert agg["error_kinds"] == {} and agg["unpriced_models"] == [] and agg["jaeger_up"] is False


def test_fetch_runs_discovers_prefixed_services_and_dedupes(monkeypatch):
    # Claude Code registers surface-specific service names (claude-code, claude-code-desktop);
    # fetch_runs must query every claude-code* service and dedupe traces seen under two names.
    calls = []

    def fake_get(path):
        calls.append(path)
        if path.startswith("/api/services"):
            return {"data": ["claude-code-desktop", "claude-code", "jaeger", "other"]}
        if "service=claude-code-desktop" in path:
            return {"data": [TRACE]}
        if "service=claude-code" in path:
            return {"data": [TRACE]}          # same trace under both -> deduped by trace_id
        return None

    monkeypatch.setattr(o, "_get", fake_get)
    out = o.fetch_runs()
    assert [r["trace_id"] for r in out["runs"]] == ["abc123"]
    assert out["agg"]["jaeger_up"] is True and out["agg"]["runs"] == 1
    svc_queries = [c for c in calls if c.startswith("/api/traces?")]
    assert len(svc_queries) == 2 and not any("service=jaeger" in c or "service=other" in c for c in svc_queries)


def test_fetch_runs_falls_back_to_configured_service(monkeypatch):
    # discovery unavailable (old Jaeger / transient error) -> query the configured SERVICE name
    def fake_get(path):
        if path.startswith("/api/services"):
            return None
        assert "service=claude-code" in path
        return {"data": []}

    monkeypatch.setattr(o, "_get", fake_get)
    out = o.fetch_runs()
    assert out["runs"] == [] and out["agg"]["jaeger_up"] is True
