"""Empty structured CLI turns keep usage but never render transport JSON."""
from types import SimpleNamespace

from backend.agent.coding_agents.cli_shared import finalize_cli_turn
from backend.agent.coding_agents.proc_exec import ProcResult


def test_empty_codex_json_is_not_a_reply_and_usage_survives(monkeypatch):
    from backend.agent.coding_agents import runner
    from frontend.ui_web import project_chats, agent_modes, group_orchestrator
    raw = '{"type":"thread.started","thread_id":"thread-1"}\n{"type":"turn.completed","usage":{"input_tokens":42,"output_tokens":0}}'
    result = finalize_cli_turn(proc=ProcResult(returncode=0, raw_tail=raw), reply='',
        streamed=False, blocks=[], session_id='', new_session='thread-1',
        usage={'input_tokens': 42, 'output_tokens': 0}, agent_label='Codex', timeout_s=30, error_text='')
    assert result.ok and result.reply_text == '' and result.output_tail == ''
    assert result.usage == {'input_tokens': 42, 'output_tokens': 0}
    assert result.upstream_session_id == 'thread-1'
    saved, events = [], []
    monkeypatch.setattr(project_chats, 'upsert_in_flight_assistant', lambda conv, msg, **kw: saved.append(msg))
    monkeypatch.setattr(project_chats, 'save_conversation', lambda conv: None)
    monkeypatch.setattr(agent_modes, 'close_changeset_run', lambda *a: None)
    monkeypatch.setattr(group_orchestrator, 'announce_private_member_talk', lambda *a, **kw: None)
    runner._emit_assistant(SimpleNamespace(id='chat'), agent_id='codex', reply=result.reply_text,
                           push=events.append, run_id='r', ok=result.ok, blocks=result.blocks)
    assert saved[0]['content'] == ''
    assert not any(e['type'] == 'text_delta' for e in events)
    assert events[-1]['type'] == 'agent_stopped'


def test_real_text_reply_survives():
    result = finalize_cli_turn(proc=ProcResult(returncode=0, raw_tail='transport'), reply='Answer',
        streamed=True, blocks=[], session_id='old', new_session='old',
        usage={'output_tokens': 2}, agent_label='Codex', timeout_s=30, error_text='')
    assert result.reply_text == 'Answer' and result.usage['output_tokens'] == 2
