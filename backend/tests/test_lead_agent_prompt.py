from __future__ import annotations

from deerflow.agents.lead_agent.prompt import apply_prompt_template


def test_prompt_includes_fixed_repo_root(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_CONTAINER_REPO_ROOT", "/workspace/deerflow")
    prompt = apply_prompt_template()
    assert "Fixed repo root: `/workspace/deerflow`" in prompt


def test_prompt_requires_local_search_before_asking_for_paths(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_CONTAINER_REPO_ROOT", "/workspace/deerflow")
    prompt = apply_prompt_template()
    assert "PROJECT-LOCAL DISCOVERY OVERRIDE" in prompt
    assert "Do not ask the user where DeerFlow is installed" in prompt


def test_prompt_does_not_include_hard_clarification_safety_rules(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_CONTAINER_REPO_ROOT", "/workspace/deerflow")
    prompt = apply_prompt_template()
    assert "MANDATORY Clarification Scenarios" not in prompt
    assert "Risky Operations" not in prompt
    assert "DO NOT proceed with guesses" not in prompt
