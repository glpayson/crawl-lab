import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.typing import Subprotocol

from crawl_lab.session import (
    CHOICES,
    VERSION,
    Session,
    SessionError,
    _creation_key,
    validate_endpoint,
)

Message = dict[str, Any]


class FakeWire:
    def __init__(self, messages: list[Message]) -> None:
        self.frames: deque[str | bytes] = deque(
            json.dumps({"msgs": [m]}) for m in messages
        )
        self.sent: list[Message] = []
        self.closed = False
        self.send_error = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))
        if self.send_error:
            raise OSError("Potentially delivered: sensitive wire details")

    async def recv(self) -> str | bytes:
        if self.frames:
            return self.frames.popleft()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


def links(text: str = "DCSS") -> Message:
    return {"msg": "set_game_links", "content": f'<a href="#play-dcss-web">{text}</a>'}


def auth() -> list[Message]:
    return [{"msg": "login_success", "username": "TestPlayer"}, links()]


def fixture() -> list[Message]:
    result: list[Message] = json.loads(
        (Path(__file__).parent / "fixtures/lifecycle-0.34.0.json").read_text()
    )
    return result


def ready() -> list[Message]:
    player: Message = {}
    for message in fixture():
        if message["msg"] == "player":
            player.update(message)
    return [
        {"msg": "version", "text": VERSION},
        player,
        {"msg": "map"},
        {"msg": "input_mode", "mode": 1},
    ]


def test_new_save_from_sanitized_live_fixture() -> None:
    async def run() -> None:
        wire = FakeWire(
            auth()
            + [{"msg": "rcfile_contents"}]
            + fixture()
            + [links("[TestPlayer, level 1 Minotaur]")]
        )
        session = Session(wire)
        await session.authenticate("TestPlayer", "secret")
        character = await session.start(new=True)
        assert (
            character.name == "TestPlayer"
            and character.hp == 19
            and character.depth == 1
        )
        assert character.turn == 0 and character.x == 0
        await session.save_and_exit()
        assert session.phase == "authenticated"
        assert [m["keycode"] for m in wire.sent if m["msg"] == "key"] == [
            98,
            102,
            98,
            19,
        ]
        assert all(m.get("keycode") != 17 for m in wire.sent)
        await session.close()
        assert wire.closed

    asyncio.run(run())


def test_resume_requires_no_creation_keys_and_preserves_summary() -> None:
    async def run() -> None:
        wire = FakeWire(auth() + [{"msg": "rcfile_contents"}] + ready())
        # Bundling several updates in one WebSocket frame must preserve order.
        wire.frames = deque(
            [json.dumps({"msgs": auth() + [{"msg": "rcfile_contents"}] + ready()})]
        )
        session = Session(wire)
        await session.authenticate("TestPlayer", "secret")
        result = await session.start(new=False)
        assert result.hp == 19 and result.turn == 0
        assert not any(m["msg"] == "key" for m in wire.sent)
        await session.close()

    asyncio.run(run())


def test_registration_is_explicit_and_does_not_fallback() -> None:
    async def run() -> None:
        wire = FakeWire([{"msg": "register_fail", "reason": "secret"}])
        session = Session(wire)
        with pytest.raises(SessionError, match="rejected") as error:
            await session.authenticate("TestPlayer", "secret", register=True)
        assert "secret" not in str(error.value)
        assert wire.sent == [
            {
                "msg": "register",
                "username": "TestPlayer",
                "password": "secret",
                "email": "",
            }
        ]
        assert wire.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "message",
    [
        {"msg": "login_fail"},
        {"msg": "close"},
        {"msg": "login_success", "username": "SomeoneElse"},
    ],
)
def test_auth_rejection_closes(message: Message) -> None:
    async def run() -> None:
        wire = FakeWire([message])
        session = Session(wire)
        with pytest.raises(SessionError):
            await session.authenticate("TestPlayer", "secret")
        assert wire.closed and session.phase == "closed"

    asyncio.run(run())


@pytest.mark.parametrize(
    "raw",
    [
        '{"msgs":{}}',
        "[]",
        "{",
        '{"msgs":[null]}',
        '{"msgs":[{"msg":1}]}',
        b"compressed",
    ],
)
def test_malformed_protocol_is_controlled(raw: str | bytes) -> None:
    async def run() -> None:
        wire = FakeWire([])
        wire.frames.append(raw)
        session = Session(wire)
        with pytest.raises(SessionError):
            await session.authenticate("TestPlayer", "secret")
        assert wire.closed

    asyncio.run(run())


def test_deadline_does_not_retry_authentication() -> None:
    async def run() -> None:
        wire = FakeWire([])
        session = Session(wire, timeout=0.01)
        with pytest.raises(SessionError, match="Timed out"):
            await session.authenticate("TestPlayer", "secret")
        assert len(wire.sent) == 1 and wire.closed
        with pytest.raises(SessionError, match="closed"):
            await session.authenticate("TestPlayer", "secret")

    asyncio.run(run())


def test_heartbeat_does_not_reset_operation_deadline() -> None:
    class PingWire(FakeWire):
        async def recv(self) -> str:
            return '{"msgs":[{"msg":"ping"}]}'

    async def run() -> None:
        wire = PingWire([])
        session = Session(wire, timeout=0.01)
        with pytest.raises(SessionError, match="Timed out"):
            await session.authenticate("TestPlayer", "secret")
        assert any(m["msg"] == "pong" for m in wire.sent)
        assert sum(m["msg"] == "login" for m in wire.sent) == 1
        assert wire.closed

    asyncio.run(run())


def test_ambiguous_send_is_not_retried_or_exposed() -> None:
    async def run() -> None:
        wire = FakeWire([])
        wire.send_error = True
        session = Session(wire)
        with pytest.raises(SessionError, match="outcome may be unknown") as error:
            await session.authenticate("TestPlayer", "secret")
        assert "sensitive" not in str(error.value)
        assert len(wire.sent) == 1 and wire.closed

    asyncio.run(run())


@pytest.mark.parametrize("slot", ["[TestPlayer, level 1 Minotaur]", "[playing]"])
def test_new_refuses_existing_save_before_play(slot: str) -> None:
    async def run() -> None:
        wire = FakeWire(auth() + [links(slot), {"msg": "rcfile_contents"}])
        session = Session(wire)
        await session.authenticate("TestPlayer", "secret")
        with pytest.raises(SessionError):
            await session.start(new=True)
        assert not any(m["msg"] == "play" for m in wire.sent)

    asyncio.run(run())


def test_new_refuses_save_even_if_initial_html_said_empty() -> None:
    async def run() -> None:
        wire = FakeWire(auth() + [{"msg": "rcfile_contents"}] + ready())
        session = Session(wire)
        await session.authenticate("TestPlayer", "secret")
        with pytest.raises(SessionError, match="existing character"):
            await session.start(new=True)
        assert not any(m["msg"] == "key" for m in wire.sent)
        assert wire.closed

    asyncio.run(run())


def test_resume_never_creates_missing_character() -> None:
    async def run() -> None:
        wire = FakeWire(auth() + [{"msg": "rcfile_contents"}] + fixture())
        session = Session(wire)
        await session.authenticate("TestPlayer", "secret")
        with pytest.raises(SessionError, match="Resume encountered"):
            await session.start(new=False)
        assert not any(m["msg"] == "key" for m in wire.sent)

    asyncio.run(run())


def test_active_account_is_not_taken_over() -> None:
    async def run() -> None:
        wire = FakeWire(
            auth()
            + [
                {"msg": "lobby_entry", "id": 42, "username": "testplayer"},
                {"msg": "rcfile_contents"},
            ]
        )
        session = Session(wire)
        await session.authenticate("TestPlayer", "secret")
        with pytest.raises(SessionError, match="active game"):
            await session.start(new=False)
        assert not any(m["msg"] == "play" for m in wire.sent)

    asyncio.run(run())


@pytest.mark.parametrize(
    "unexpected",
    [
        {"msg": "version", "text": "Dungeon Crawl Stone Soup 0.35.0"},
        {"msg": "input_mode", "mode": 5},
        {"msg": "input_mode", "mode": 7},
        {"msg": "input_mode", "mode": 1},
        {"msg": "ui-push", "type": "seed-selection"},
        {"msg": "menu"},
        {"msg": "go_lobby"},
    ],
)
def test_unknown_startup_sends_no_keys(unexpected: Message) -> None:
    async def run() -> None:
        wire = FakeWire(auth() + [{"msg": "rcfile_contents"}, unexpected])
        session = Session(wire)
        await session.authenticate("TestPlayer", "secret")
        with pytest.raises(SessionError):
            await session.start(new=True)
        assert not any(m["msg"] == "key" for m in wire.sent)

    asyncio.run(run())


@pytest.mark.parametrize(
    "ending",
    [
        [{"msg": "go_lobby"}],
        [{"msg": "game_ended", "reason": "dead"}],
        [{"msg": "input_mode", "mode": 7}],
        [{"msg": "ui-push", "type": "confirmation"}],
    ],
)
def test_save_requires_confirmed_saved_exit(ending: list[Message]) -> None:
    async def run() -> None:
        wire = FakeWire(auth() + [{"msg": "rcfile_contents"}] + ready() + ending)
        session = Session(wire)
        await session.authenticate("TestPlayer", "secret")
        await session.start(new=False)
        with pytest.raises(SessionError):
            await session.save_and_exit()
        assert [m["keycode"] for m in wire.sent if m["msg"] == "key"] == [19]
        assert wire.closed

    asyncio.run(run())


def test_creation_uses_label_not_hardcoded_key() -> None:
    message: Message = {
        "msg": "ui-push",
        "type": "newgame-choice",
        "main-items": {
            "menu_id": "species-main",
            "buttons": [{"labels": ["<white>z - Minotaur"], "hotkey": 122}],
        },
    }
    assert _creation_key(message, *CHOICES[0]) == 122
    message["main-items"]["buttons"][0]["hotkey"] = 17
    with pytest.raises(SessionError, match="Unsafe"):
        _creation_key(message, *CHOICES[0])


def test_operation_exclusivity_and_cancellation() -> None:
    async def run() -> None:
        wire = FakeWire([])
        session = Session(wire)
        task = asyncio.create_task(session.authenticate("TestPlayer", "secret"))
        await asyncio.sleep(0)
        with pytest.raises(SessionError, match="in progress"):
            await session.authenticate("TestPlayer", "secret")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wire.closed
        assert len(wire.sent) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost",
        "ws://example.com/socket",
        "ws://user:secret@localhost/socket",
        "wss://example.com/socket?token=secret",
        "ws://localhost:invalid",
        "ws://localhost/#secret",
    ],
)
def test_endpoint_safety(url: str) -> None:
    with pytest.raises(SessionError):
        validate_endpoint(url)


@pytest.mark.parametrize(
    "url",
    [
        "ws://localhost:8080/socket",
        "ws://127.0.0.1:8080/socket",
        "ws://[::1]:8080/socket",
        "wss://example.com/socket",
    ],
)
def test_allowed_endpoints(url: str) -> None:
    validate_endpoint(url)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout(timeout: float) -> None:
    with pytest.raises(SessionError):
        Session(FakeWire([]), timeout=timeout)


def test_real_websocket_negotiation_and_login() -> None:
    async def run() -> None:
        async def handler(ws: ServerConnection) -> None:
            assert ws.subprotocol == "no-compression"
            await ws.send(json.dumps({"msgs": [{"msg": "lobby_complete"}]}))
            request = json.loads(await ws.recv())
            assert request["msg"] == "login"
            await ws.send(json.dumps({"msgs": auth()}))
            await ws.wait_closed()

        async with serve(
            handler,
            "127.0.0.1",
            0,
            subprotocols=[Subprotocol("no-compression")],
            compression=None,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            session = await Session.open(f"ws://127.0.0.1:{port}/socket")
            await session.authenticate("TestPlayer", "secret")
            assert session.phase == "authenticated"
            await session.close()

    asyncio.run(run())


def test_unnegotiated_compression_is_rejected() -> None:
    async def run() -> None:
        async def handler(ws: ServerConnection) -> None:
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0, compression=None) as server:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises(SessionError, match="negotiate"):
                await Session.open(f"ws://127.0.0.1:{port}/socket")

    asyncio.run(run())
