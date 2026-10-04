"""Bounded session lifecycle for DCSS 0.34.0 WebTiles.

Protocol reference: crawl/crawl, tag 0.34.0, webserver/webtiles/ws_handler.py.
Only lifecycle input is exposed. This is not a gameplay/state-model API.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException
from websockets.typing import Subprotocol

Message = dict[str, Any]
Phase = Literal["lobby", "authenticated", "playing", "closed"]
DEFAULT_URL = "ws://localhost:8080/socket"
DEFAULT_GAME = "dcss-web"
VERSION = "Dungeon Crawl Stone Soup 0.34.0"
CHOICES = (
    ("species-main", "Minotaur"),
    ("background-main", "Berserker"),
    ("weapon-main", "mace"),
)


class SessionError(Exception):
    """A safe, credential-free error that can be printed by the CLI."""


class Wire(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class Character:
    """Minimal preservation evidence, not a complete game observation."""

    name: str
    species: str
    level: int
    hp: int
    max_hp: int
    turn: int
    place: str
    depth: int
    x: int
    y: int


def validate_endpoint(url: str) -> None:
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port
    except ValueError:
        raise SessionError("Invalid WebTiles URL.") from None
    if (
        parts.scheme not in {"ws", "wss"}
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise SessionError("Use a ws/wss URL without credentials, query, or fragment.")
    if parts.scheme == "ws" and host != "localhost":
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise SessionError(
                "Unencrypted ws is allowed only on loopback; use wss remotely."
            )


def _validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise SessionError("Timeout must be a positive finite number.")


class _GameLinks(HTMLParser):
    """Read only play anchors; never execute or print server HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.links: dict[str, str] = {}
        self.current: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self.current = href[6:] if href.startswith("#play-") else None
            if self.current is not None:
                self.links[self.current] = ""

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.links[self.current] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.current = None


class Session:
    def __init__(self, wire: Wire, *, timeout: float = 15) -> None:
        _validate_timeout(timeout)
        self._wire = wire
        self.timeout = timeout
        self.phase: Phase = "lobby"
        self.username: str | None = None
        self.character: Character | None = None
        self._pending: deque[Message] = deque()
        self._busy = False
        self._links: dict[str, str] | None = None
        self._active: dict[str, str] = {}

    @classmethod
    async def open(cls, url: str = DEFAULT_URL, *, timeout: float = 15) -> Session:
        validate_endpoint(url)
        _validate_timeout(timeout)
        try:
            wire = await connect(
                url,
                subprotocols=[Subprotocol("no-compression")],
                compression=None,
                proxy=None,
                open_timeout=timeout,
                close_timeout=2,
                max_size=1_048_576,
                max_queue=16,
            )
        except OSError, TimeoutError, WebSocketException, ValueError:
            raise SessionError("Could not connect to WebTiles.") from None
        session = cls(wire, timeout=timeout)
        if wire.subprotocol != "no-compression":
            await session.close()
            raise SessionError("Server did not negotiate no-compression.")
        async with session._operation("connect", "lobby"):
            await session._until("lobby_complete")
        return session

    async def close(self) -> None:
        """Disconnect only. Never send abandon, save, or other game input here."""
        if self.phase == "closed":
            return
        self.phase = "closed"
        try:
            async with asyncio.timeout(3):
                await self._wire.close()
        except TimeoutError, OSError, WebSocketException:
            pass

    @asynccontextmanager
    async def _operation(self, name: str, expected: Phase) -> AsyncIterator[None]:
        if self._busy:
            raise SessionError("Another session operation is in progress.")
        if self.phase != expected:
            raise SessionError(f"Cannot {name} while session is {self.phase}.")
        self._busy = True
        try:
            async with asyncio.timeout(self.timeout):
                yield
        except TimeoutError:
            await self.close()
            raise SessionError(
                f"Timed out during {name}; outcome may be unknown. No retry sent."
            ) from None
        except OSError, WebSocketException:
            await self.close()
            raise SessionError(
                f"Connection failed during {name}; outcome may be unknown. No retry sent."
            ) from None
        except BaseException:
            await self.close()
            raise
        finally:
            self._busy = False

    async def _send(self, message: Message) -> None:
        await self._wire.send(json.dumps(message))

    async def _next(self) -> Message:
        while True:
            if not self._pending:
                raw = await self._wire.recv()
                if not isinstance(raw, str):
                    raise SessionError("Expected uncompressed WebTiles text.")
                try:
                    envelope = json.loads(raw)
                except ValueError, RecursionError:
                    raise SessionError("Invalid WebTiles JSON.") from None
                messages = envelope.get("msgs") if isinstance(envelope, dict) else None
                if (
                    not isinstance(messages, list)
                    or len(messages) > 10_000
                    or any(
                        not isinstance(m, dict) or not isinstance(m.get("msg"), str)
                        for m in messages
                    )
                ):
                    raise SessionError("Invalid WebTiles message envelope.")
                self._pending.extend(messages)
                if not messages:
                    # Yield so an empty-message flood cannot starve the deadline.
                    await asyncio.sleep(0)
                    continue
            message = self._pending.popleft()
            await asyncio.sleep(0)
            kind = message["msg"]
            if kind == "ping":
                await self._send({"msg": "pong"})
                continue
            if kind in {
                "login_fail",
                "register_fail",
                "login_required",
                "auth_error",
                "set_account_hold",
                "close",
                "error",
                "logout",
            }:
                raise SessionError(
                    "WebTiles rejected the operation or closed the session."
                )
            if kind == "lobby_clear":
                self._active.clear()
            elif kind == "lobby_entry":
                if type(message.get("id")) not in {int, str} or not isinstance(
                    message.get("username"), str
                ):
                    raise SessionError(
                        "Invalid active-game listing; refusing uncertain ownership."
                    )
                self._active[str(message["id"])] = message["username"].casefold()
            elif kind == "lobby_remove":
                self._active.pop(str(message.get("id")), None)
            elif kind == "set_game_links":
                content = message.get("content")
                if not isinstance(content, str):
                    raise SessionError("Invalid game links.")
                parser = _GameLinks()
                parser.feed(content)
                self._links = {
                    key: value.strip() for key, value in parser.links.items()
                }
            return message

    async def _until(self, kind: str) -> Message:
        while True:
            message = await self._next()
            if message["msg"] == kind:
                return message

    async def authenticate(
        self, username: str, password: str, *, register: bool = False
    ) -> None:
        if not username or not password:
            raise SessionError("Username and password are required.")
        async with self._operation("authenticate", "lobby"):
            payload = {
                "msg": "register" if register else "login",
                "username": username,
                "password": password,
            }
            if register:
                payload["email"] = ""
            await self._send(payload)
            reply = await self._until("login_success")
            canonical = reply.get("username")
            if (
                not isinstance(canonical, str)
                or canonical.casefold() != username.casefold()
            ):
                raise SessionError("Server authenticated an unexpected account.")
            self.username = canonical
            await self._until("set_game_links")
            self.phase = "authenticated"

    async def start(self, *, new: bool, game_id: str = DEFAULT_GAME) -> Character:
        async with self._operation("start" if new else "resume", "authenticated"):
            # A read-only round trip drains queued lobby updates before sending play.
            # Save-info HTML can initially contain placeholders, so it is only a guard;
            # actual creation screens / ready state determine new versus resume.
            if self._links is None or game_id not in self._links:
                raise SessionError("Game is unavailable or its save slot is blocked.")
            await self._send({"msg": "get_rc", "game_id": game_id})
            await self._until("rcfile_contents")
            if (
                self.username is None
                or self.username.casefold() in self._active.values()
            ):
                raise SessionError(
                    "Account already has an active game; refusing takeover."
                )
            if self._links is None or game_id not in self._links:
                raise SessionError("Game is unavailable or its save slot is blocked.")
            slot = self._links[game_id]
            if slot == "[playing]":
                raise SessionError("Save is in use; refusing takeover.")
            if new and slot.startswith("["):
                raise SessionError("A save already exists; use resume instead.")
            await self._send({"msg": "play", "game_id": game_id})
            player: Message = {}
            choices = 0
            mapped = False
            version_seen = False
            while True:
                message = await self._next()
                kind = message["msg"]
                if kind == "version":
                    if message.get("text") != VERSION:
                        raise SessionError("This adapter supports DCSS 0.34.0 only.")
                    version_seen = True
                elif kind == "ui-push":
                    if not new:
                        raise SessionError(
                            "Resume encountered a creation/prompt screen; no choice sent."
                        )
                    if not version_seen or choices >= len(CHOICES):
                        raise SessionError("Unexpected character-creation sequence.")
                    key = _creation_key(message, *CHOICES[choices])
                    await self._send({"msg": "key", "keycode": key})
                    choices += 1
                elif kind in {"menu", "input", "game_ended", "go_lobby"}:
                    raise SessionError("Unexpected screen or game exit during startup.")
                elif kind == "player":
                    player.update(message)
                elif kind == "map":
                    mapped = True
                elif kind == "input_mode":
                    mode = message.get("mode")
                    if type(mode) is not int or mode not in {0, 1}:
                        raise SessionError(
                            "Unsupported input prompt during startup; no key sent."
                        )
                    if mode == 1:
                        if not version_seen or not mapped:
                            raise SessionError(
                                "Game reported ready without version/map evidence."
                            )
                        if new and choices != len(CHOICES):
                            raise SessionError(
                                "New-game request encountered an existing character; no gameplay input sent."
                            )
                        result = _character(player, self.username)
                        if new and result.species != "Minotaur":
                            raise SessionError("Unexpected character species.")
                        self.character = result
                        self.phase = "playing"
                        return result

    async def save_and_exit(self) -> None:
        async with self._operation("save", "playing"):
            await self._send({"msg": "key", "keycode": 19})  # Ctrl-S, NOT Ctrl-Q.
            saved = False
            while True:
                message = await self._next()
                kind = message["msg"]
                if kind == "game_ended":
                    if message.get("reason") != "saved":
                        raise SessionError("Game ended without a confirmed save.")
                    saved = True
                elif kind == "go_lobby":
                    if not saved:
                        raise SessionError("Lobby reached without a confirmed save.")
                    self.phase = "authenticated"
                    self._links = None
                    await self._until("set_game_links")
                    return
                elif kind in {"ui-push", "menu", "input"} or (
                    kind == "input_mode" and message.get("mode") not in {0, 1}
                ):
                    raise SessionError(
                        "Unexpected prompt during save; no confirmation key sent."
                    )


def _creation_key(message: Message, menu_id: str, selection: str) -> int:
    menu = message.get("main-items")
    if (
        message.get("type") != "newgame-choice"
        or not isinstance(menu, dict)
        or menu.get("menu_id") != menu_id
    ):
        raise SessionError("Unsupported character-creation screen.")
    buttons = menu.get("buttons")
    if not isinstance(buttons, list):
        raise SessionError("Malformed character-creation choices.")
    matches: list[int] = []
    for button in buttons:
        if not isinstance(button, dict):
            raise SessionError("Malformed character-creation choice.")
        labels = button.get("labels", [button.get("label", "")])
        if not isinstance(labels, list) or any(
            not isinstance(label, str) for label in labels
        ):
            raise SessionError("Malformed character-creation labels.")
        for label in labels:
            text = re.sub(r"<[^>]*>", "", label).strip()
            hotkey, separator, name = text.partition(" - ")
            if separator and name.casefold() == selection.casefold():
                key = button.get("hotkey")
                if (
                    len(hotkey) != 1
                    or not hotkey.isascii()
                    or not hotkey.isalpha()
                    or type(key) is not int
                    or key != ord(hotkey)
                ):
                    raise SessionError("Unsafe character-creation hotkey.")
                matches.append(key)
    if len(matches) != 1:
        raise SessionError("Supported character choice is missing or ambiguous.")
    return matches[0]


def _character(player: Message, username: str) -> Character:
    def number(key: str) -> int:
        value = player.get(key)
        if type(value) is not int or value < 0:
            raise SessionError("Incomplete player summary.")
        return value

    name, species, place = (player.get(k) for k in ("name", "species", "place"))
    position = player.get("pos")
    if (
        not isinstance(name, str)
        or not isinstance(species, str)
        or not species
        or not isinstance(place, str)
        or not place
        or name != username
    ):
        raise SessionError("Missing or unexpected character identity.")
    if not isinstance(position, dict) or any(
        type(position.get(k)) is not int for k in ("x", "y")
    ):
        raise SessionError("Missing player position.")
    if player.get("wizard") != 0 or player.get("explore") != 0:
        raise SessionError("Only ordinary, non-wizard games are supported.")
    result = Character(
        name,
        species,
        number("xl"),
        number("hp"),
        number("hp_max"),
        number("turn"),
        place,
        number("depth"),
        position["x"],
        position["y"],
    )
    if result.hp <= 0 or result.level < 1 or result.hp > result.max_hp:
        raise SessionError("Player summary isn't a living character.")
    return result
