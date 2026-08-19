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
    # cost = (100*15 + 50*75 + 200*15*1.25)/1e6 = 9000/1e6
    assert abs(r["cost_usd"] - 0.009) < 1e-6
    assert r["title"] == "claude_code.interaction"


def test_partial_trace_without_interaction_is_skipped():
    assert o.summarize_trace(PARTIAL) is None      # the quirk fix — no interaction root => not a run
    assert o.summarize_trace({"traceID": "e", "spans": []}) is None


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
