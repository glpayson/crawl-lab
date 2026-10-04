import asyncio
from typing import Literal

import pytest

from crawl_lab.cli import main, run_lifecycle
from crawl_lab.session import Session, SessionError
from tests.test_session import FakeWire, auth, fixture, links, ready


def test_missing_credentials_do_not_connect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CRAWL_LAB_USERNAME", raising=False)
    monkeypatch.delenv("CRAWL_LAB_PASSWORD", raising=False)
    assert main(["resume"]) == 2
    assert "CRAWL_LAB_PASSWORD" in capsys.readouterr().err


def test_help_needs_no_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "resume" in help_text
    assert "demo" not in help_text


def test_demo_is_not_a_cli_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as result:
        main(["demo"])
    assert result.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_password_flag_is_not_supported(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as result:
        main(["resume", "--password", "not-a-real-secret"])
    assert result.value.code == 2
    capsys.readouterr()


def mock_session(monkeypatch: pytest.MonkeyPatch, wire: FakeWire) -> list[str]:
    opened: list[str] = []

    async def open_session(cls: type[Session], url: str, *, timeout: float) -> Session:
        opened.append(url)
        return cls(wire, timeout=timeout)

    monkeypatch.setattr(Session, "open", classmethod(open_session))
    return opened


def saved_resume_wire() -> FakeWire:
    return FakeWire(
        auth()
        + [{"msg": "rcfile_contents"}]
        + ready()
        + [
            {"msg": "game_ended", "reason": "saved"},
            {"msg": "go_lobby"},
            links("[saved]"),
        ]
    )


@pytest.mark.parametrize("command", ["register", "new", "resume"])
def test_command_uses_one_session(
    monkeypatch: pytest.MonkeyPatch, command: Literal["register", "new", "resume"]
) -> None:
    if command == "register":
        wire = FakeWire(auth())
    elif command == "new":
        wire = FakeWire(
            auth() + [{"msg": "rcfile_contents"}] + fixture() + [links("[saved]")]
        )
    else:
        wire = saved_resume_wire()
    opened = mock_session(monkeypatch, wire)
    result = asyncio.run(
        run_lifecycle(
            command,
            url="ws://localhost:8080/socket",
            username="TestPlayer",
            password="secret",
        )
    )
    assert len(opened) == 1
    assert wire.closed
    assert result["status"] == ("registered" if command == "register" else "saved")
    assert wire.sent[0]["msg"] == ("register" if command == "register" else "login")
    assert sum(m.get("keycode") == 19 for m in wire.sent) == (
        0 if command == "register" else 1
    )
    assert "preserved" not in result
    if command == "register":
        assert not any(m["msg"] == "play" for m in wire.sent)


def test_command_closes_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = FakeWire([{"msg": "login_fail"}])
    mock_session(monkeypatch, wire)
    with pytest.raises(SessionError):
        asyncio.run(
            run_lifecycle(
                "resume",
                url="ws://localhost:8080/socket",
                username="TestPlayer",
                password="secret",
            )
        )
    assert wire.closed


def test_cli_does_not_print_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_session(monkeypatch, saved_resume_wire())
    monkeypatch.setenv("CRAWL_LAB_USERNAME", "TestPlayer")
    monkeypatch.setenv("CRAWL_LAB_PASSWORD", "SECRET_SHOULD_NOT_APPEAR")
    assert main(["resume"]) == 0
    output = capsys.readouterr()
    assert "SECRET_SHOULD_NOT_APPEAR" not in output.out + output.err
    assert '"status": "saved"' in output.out
