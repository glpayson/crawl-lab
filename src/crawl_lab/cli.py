"""Finite lifecycle commands. Credentials are never accepted on the command line."""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from typing import Literal

from crawl_lab.session import (
    DEFAULT_GAME,
    DEFAULT_URL,
    Session,
    SessionError,
)


async def run_lifecycle(
    command: Literal["register", "new", "resume"],
    *,
    url: str,
    username: str,
    password: str,
    game_id: str = DEFAULT_GAME,
    timeout: float = 15,
) -> dict[str, object]:
    session = await Session.open(url, timeout=timeout)
    try:
        await session.authenticate(username, password, register=command == "register")
        if command == "register":
            return {"status": "registered"}
        character = await session.start(new=command == "new", game_id=game_id)
        await session.save_and_exit()
        return {"status": "saved", "character": asdict(character)}
    finally:
        await session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DCSS 0.34.0")
    parser.add_argument("--url", default=os.getenv("CRAWL_LAB_URL", DEFAULT_URL))
    parser.add_argument(
        "--game-id", default=os.getenv("CRAWL_LAB_GAME_ID", DEFAULT_GAME)
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15,
        help="Deadline per operation in seconds (default: 15)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "register", help="Register a new account; never overwrite or fall back to login"
    )
    commands.add_parser(
        "new", help="Create a Minotaur Berserker with a mace, then save-and-exit"
    )
    commands.add_parser(
        "resume", help="Resume an existing character, then save-and-exit"
    )
    args = parser.parse_args(argv)

    username = os.getenv("CRAWL_LAB_USERNAME", "")
    password = os.getenv("CRAWL_LAB_PASSWORD", "")

    if not username or not password:
        print(
            "Set CRAWL_LAB_USERNAME and CRAWL_LAB_PASSWORD (use a dedicated test account).",
            file=sys.stderr,
        )
        return 2

    try:
        result = asyncio.run(
            run_lifecycle(
                args.command,
                url=args.url,
                username=username,
                password=password,
                game_id=args.game_id,
                timeout=args.timeout,
            )
        )
    except SessionError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Interrupted; connection closed. Save status is not confirmed.",
            file=sys.stderr,
        )
        return 130
    print(json.dumps(result, indent=2))
    return 0
