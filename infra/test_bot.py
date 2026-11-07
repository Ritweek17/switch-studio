"""Offline tests for Switch protocol parsing and M1 handoffs."""

from __future__ import annotations

import json
import socket
import struct
from pathlib import Path

from infra.bot import (
    MAX_SHIP_MESSAGE_CHARS,
    CommandProcessor,
    RoomWorkflow,
    RunStore,
    SwitchClient,
    WebSocket,
    event_is_human_message,
    format_shipped_message,
    message_from_event,
)
from schemas.validate import load_document, validate

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "schemas" / "examples"


class FakeClient:
    agent_id = "infra-id"
    _room_id = "room-1"

    def __init__(self):
        self.posts: list[tuple[str, str]] = []

    def post_message(self, room_id: str, content: str) -> None:
        self.posts.append((room_id, content))


def _write_doc(path: Path, source: Path) -> dict:
    doc = load_document(source)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_websocket_url_uses_current_switch_connection_protocol(tmp_path: Path):
    state = tmp_path / "runs" / "demo-001" / "switch-connection.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"connection_id": "conn-123", "cursor": 87, "generation": 4, "room_id": "room-1"}))
    client = SwitchClient("https://switch.example", "agent/id", "secret", "room-1", state_path=state)
    scheme, host, port, host_header, path = client._socket_url("room-1")
    assert (scheme, host, port, host_header) == ("wss", "switch.example", 443, "switch.example")
    assert "/agents/agent%2Fid/connection/ws?" in path
    assert "start_from=87" in path
    assert "expected_generation=4" in path


def test_websocket_reads_server_text_frame_and_sends_masked_client_frame():
    left, right = socket.socketpair()
    try:
        server = WebSocket(left)
        client = WebSocket(right)
        payload = b'{"event":"ping"}'
        client.sock.sendall(bytes([0x81, len(payload)]) + payload)
        fin, opcode, received = server.receive_frame()
        assert (fin, opcode, received) == (True, 1, payload)

        server.send_json({"type": "pong", "cursor": 9})
        first, second = client.receive_exactly(2)
        assert first == 0x81 and second & 0x80
        length = second & 0x7F
        mask = client.receive_exactly(4)
        masked = client.receive_exactly(length)
        decoded = bytes(value ^ mask[i % 4] for i, value in enumerate(masked))
        assert json.loads(decoded) == {"type": "pong", "cursor": 9}
    finally:
        left.close()
        right.close()


def test_switch_domain_event_normalizes_and_classifies_senders():
    event = {"event": "message", "id": 5, "data": {"type": "message", "sequence": 5, "payload": {"body": "approve all", "sender_name": "Ritweek", "on_behalf_of": "ritweek"}}}
    assert message_from_event(event) == ("approve all", "ritweek")
    assert event_is_human_message(event, bot_agent_id="infra-id")
    agent_event = {"type": "message", "payload": {"body": "draft", "sender": "scout-id", "sender_name": "codex.scout.ritweek"}}
    assert not event_is_human_message(agent_event, known_agent_identities={"codex.scout.ritweek", "scout-id"})
    assert not event_is_human_message({"type": "ping", "payload": {"sender_name": "Ritweek"}})


def test_workflow_kicks_off_scout_and_advances_facts_to_writer(tmp_path: Path):
    store = RunStore(tmp_path, "demo-001")
    snapshot = load_document(EXAMPLES / "snapshot.json")
    (store.run_dir / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    client = FakeClient()
    workflow = RoomWorkflow(store, client, tmp_path)
    workflow.kickoff("room-1")
    workflow.kickoff("room-1")
    assert len(client.posts) == 1
    assert "@codex.scout.ritweek" in client.posts[-1][1]

    facts = load_document(EXAMPLES / "facts.json")
    store.store_document(facts)
    workflow._advance_document(facts)
    assert "@codex.writer.ritweek" in client.posts[-1][1]


def test_workflow_sends_human_commands_to_decision_log(tmp_path: Path):
    store = RunStore(tmp_path, "demo-001")
    draft = load_document(EXAMPLES / "draft.r0.json")
    (store.run_dir / "draft.r0.json").write_text(json.dumps(draft), encoding="utf-8")
    client = FakeClient()
    workflow = RoomWorkflow(store, client, tmp_path)
    workflow.room_id = "room-1"
    event = {"type": "message", "payload": {"body": "approve all", "sender_name": "Ritweek", "on_behalf_of": "ritweek"}}
    workflow.handle(event)
    decisions = store.decisions()
    assert len(decisions) == 1
    assert decisions[0]["command"] == "approve_all"
    assert decisions[0]["result"]["accepted"] is True
    assert "approve_all accepted" in client.posts[-1][1]


def test_human_command_sequence_verifies_edits_and_ships(tmp_path: Path):
    store = RunStore(ROOT, "demo-001", runs_dir=tmp_path / "runs")
    snapshot = load_document(EXAMPLES / "snapshot.json")
    facts = load_document(EXAMPLES / "facts.json")
    draft = load_document(EXAMPLES / "draft.r0.json")
    for name, document in (("snapshot.json", snapshot), ("facts.json", facts), ("draft.r0.json", draft)):
        (store.run_dir / name).write_text(json.dumps(document), encoding="utf-8")
    commands = CommandProcessor(store, repo_root=ROOT)
    assert commands.process("approve S1", "Ritweek")["result"]["accepted"] is True
    assert commands.process("reject S2: remove unsupported detail", "Ritweek")["result"]["accepted"] is True
    edit = commands.process("edit S4: Exactly 3 contributors landed commits. [F4][F5]", "Ritweek")
    assert edit["result"]["accepted"] is True
    assert edit["facts"] == ["F4", "F5"]
    assert commands.process("approve all", "Ritweek")["result"]["accepted"] is True
    ship = commands.process("ship", "Ritweek")
    assert ship["result"]["accepted"] is True
    assert (store.run_dir / "final.md").is_file()
    decisions = store.decisions()
    assert [decision["command"] for decision in decisions] == ["approve", "reject", "edit", "approve_all", "ship"]
    assert all(not validate(decision) for decision in decisions)


def test_ship_over_mock_socket_posts_final_markdown_to_room(tmp_path: Path):
    store = RunStore(ROOT, "demo-001", runs_dir=tmp_path / "runs")
    snapshot = load_document(EXAMPLES / "snapshot.json")
    facts = load_document(EXAMPLES / "facts.json")
    draft = load_document(EXAMPLES / "draft.r0.json")
    for name, document in (("snapshot.json", snapshot), ("facts.json", facts), ("draft.r0.json", draft)):
        (store.run_dir / name).write_text(json.dumps(document), encoding="utf-8")

    commands = CommandProcessor(store, repo_root=ROOT)
    assert commands.process("reject S4: remove uncited sentence", "Ritweek")["result"]["accepted"] is True
    assert commands.process("approve all", "Ritweek")["result"]["accepted"] is True

    client = FakeClient()
    workflow = RoomWorkflow(store, client, ROOT)
    workflow.room_id = "room-1"

    left, right = socket.socketpair()
    try:
        server_ws = WebSocket(left)
        bot_ws = WebSocket(right)
        ship_frame = {
            "event": "message",
            "id": 12,
            "data": {
                "type": "message",
                "sequence": 12,
                "payload": {"body": "ship", "sender_name": "Ritweek", "on_behalf_of": "ritweek"},
            },
        }
        server_ws.send_json(ship_frame)
        fin, opcode, raw = bot_ws.receive_frame()
        assert (fin, opcode) == (True, 1)
        workflow.handle(json.loads(raw.decode("utf-8")))
    finally:
        left.close()
        right.close()

    final_path = store.run_dir / "final.md"
    assert final_path.is_file()
    final_text = final_path.read_text(encoding="utf-8").strip()

    assert len(client.posts) == 2
    assert client.posts[0] == ("room-1", "Run demo-001: ship accepted.")
    shipped_room_id, shipped_msg = client.posts[1]
    assert shipped_room_id == "room-1"
    assert "demo-001" in shipped_msg.splitlines()[0]
    assert final_text in shipped_msg
    assert "**Sources**" in shipped_msg


def test_format_shipped_message_truncates_when_final_md_exceeds_limit():
    oversized = "Verified sentence with citations. [F1]\n" * 200
    msg = format_shipped_message("demo-001", oversized, max_chars=300)
    assert len(msg) <= 300
    assert "demo-001" in msg.splitlines()[0]
    assert "runs/demo-001/final.md" in msg
    assert MAX_SHIP_MESSAGE_CHARS >= 1000

