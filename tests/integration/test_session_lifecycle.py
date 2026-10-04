"""Opt-in only: verify save/resume using an existing dedicated local character."""

import asyncio
import os
from urllib.parse import urlsplit

import pytest

from crawl_lab.session import DEFAULT_GAME, DEFAULT_URL, Character, Session


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("CRAWL_LAB_LIVE") != "1",
    reason="set CRAWL_LAB_LIVE=1 for the local lifecycle check",
)
def test_save_disconnect_and_resume_preserves_character() -> None:
    url = os.getenv("CRAWL_LAB_URL", DEFAULT_URL)
    username = os.environ["CRAWL_LAB_USERNAME"]
    password = os.environ["CRAWL_LAB_PASSWORD"]
    game_id = os.getenv("CRAWL_LAB_GAME_ID", DEFAULT_GAME)
    # Integration tests must never fall through to a public server / personal save.
    assert urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}
    assert username.casefold().startswith("clab"), (
        "Use a dedicated clab-prefixed test account"
    )

    async def visit() -> Character:
        session = await Session.open(url)
        try:
            await session.authenticate(username, password)
            character = await session.start(new=False, game_id=game_id)
            await session.save_and_exit()
            return character
        finally:
            await session.close()

    async def check_preservation() -> None:
        # Capture the summary, save, and disconnect before opening a new session.
        character_before_save = await visit()
        character_after_resume = await visit()
        # Both visits save before disconnecting, even if this assertion fails.
        # No gameplay turns were taken. Compare reported fields, not the entire save.
        assert character_before_save == character_after_resume

    asyncio.run(check_preservation())
