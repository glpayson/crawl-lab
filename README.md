# Crawl Lab

A lab for experimenting with AI decision backends that play Dungeon Crawl Stone Soup. Game connections are kept separate from decision policies; TypeSafe AI's Jev is the first planned experiment, not a permanent dependency of the architecture.

**Current milestone:** programmatic session lifecycle on local DCSS 0.34.0: create a character, save-and-exit, reconnect, and resume. AI decisions, ordinary gameplay commands, and a general game-state model are not implemented yet.

## Prerequisites

- A running Docker engine and Docker Compose with `up --wait` support.
- Port 8080 available on localhost.
- On Apple Silicon, a runtime that supports AMD64 containers. This setup has been exercised with Docker Desktop's emulation.

The third-party [nkhoit/dcss-webtiles](https://github.com/nkhoit/dcss-webtiles) image is pinned to DCSS 0.34.0 and an immutable image digest in `compose.yaml`. It is an AMD64 image, not a native ARM64 build.

The commands below use your current Docker context. On macOS, start Docker Desktop and verify `docker info` first. If needed, explicitly select that engine for a command with `docker --context desktop-linux compose ...`; no global context change is required.

## Start and play

From the repository root:

```
docker compose up -d --wait --wait-timeout 120
```

Open **http://localhost:8080/**, register a local account, and select **DCSS** to start a character. There are no pre-created credentials. Use a test password that is not used elsewhere.

Only `127.0.0.1:8080` is published. This is a localhost-only HTTP setup, not a hardened public deployment.

The WebTiles WebSocket endpoint is `ws://localhost:8080/socket`. The Python lifecycle client below uses it; no decision backend is configured.

## Python session lifecycle (milestone 1)

Requires Python 3.14, [uv](https://docs.astral.sh/uv/), and the running local server.
The transport uses pinned `websockets` and the official WebTiles `no-compression`
subprotocol, not the unofficial `dcss-api` wrapper. Only DCSS 0.34.0 is supported
by this first adapter.

```
uv sync
uv run crawl-lab --help
cp .env.example .env
chmod 600 .env
```

Edit `.env` with a **dedicated test account** and a unique local password. Do not
use your personal saved character. Credentials are read from environment variables,
not command-line flags, and `.env` is Git-ignored. The client does not load it
implicitly; `uv run --env-file .env` does.

Register once, then create a Minotaur Berserker with a mace:

```
uv run --env-file .env crawl-lab register
uv run --env-file .env crawl-lab new
```

Registration failure does not fall back to login. To resume the saved character:

```
uv run --env-file .env crawl-lab resume
```

Both `new` and `resume` **save-and-exit before returning**, reporting
`"status": "saved"` and the character summary. They do not take gameplay turns.
The reconnect-and-compare acceptance workflow lives in the integration test suite,
not in a CLI command.
They do not leave a background game-controller process running. The Python
`Session` API exposes `authenticate`, `start(new=...)`, `save_and_exit`, and
`close` separately for later milestones. Always close it in a `finally` block.
`close()` only disconnects; it never sends a save or abandonment command.

### Safety and limits

- No permanent quit/abandon operation is exposed. Save uses Ctrl-S, never Ctrl-Q.
- New-game requests refuse advertised saves and verify creation screens before
  selecting anything. Resume never answers a creation screen.
- Refuse an account advertised as playing; keep the account exclusive to this
  client while operating. WebTiles does not provide an atomic ownership lock to
  clients, so simultaneous logins are not supported. Watch from a separate
  spectator session, not another controller logged in as the test account.
- Initial lobby save information can be a placeholder. A `new` request may open
  an existing character before detecting the mismatch, but sends no gameplay or
  abandonment keys. Likewise a missing-save `resume` may reach creation, then
  disconnect without choosing anything. These are errors, not successful starts.
- Each operation has a deadline (default 15 seconds), plus bounded connection
  cleanup. Unknown prompts, unsupported versions, malformed messages, rejected
  authentication, and unexpected exits stop the session. No blind retry or
  automatic confirmation. After a timeout/disconnection, save status can be
  **unknown**; inspect the lobby before taking further action.
- The endpoint and game ID are configurable via `.env` or `--url` / `--game-id`
  before the subcommand. Unencrypted `ws` is restricted to loopback; use `wss`
  remotely. Remote compatibility is untested, and automation requires operator
  permission. No proxy is selected implicitly from the environment.

### Development and verification

```
just verify
just lint
```

Default tests are offline (including a local fake WebSocket server), with the
real-DCSS test skipped. `just verify` runs pytest and strict mypy, records output
in ignored `dev/verify.log`, and launches the repository's configured independent
judge on success. Judge results are review claims, not automatic fixes. The
inherited `serve` and `explore` recipes are placeholders; no HTTP app or fixture
DB exists.

After creating a dedicated **clab-prefixed** local test account and its character,
opt into the real save/resume test:

```
CRAWL_LAB_LIVE=1 uv run --env-file .env just verify
```

`tests/integration/test_session_lifecycle.py` captures the character summary,
saves and disconnects, then reconnects and resumes in a second session. It saves
again before asserting that name/species/level/HP/turn/place/depth/position match.
This compares reported fields, not the entire save file or hidden engine state.

The test only resumes/saves that existing test character; it never registers
accounts, abandons characters, takes gameplay turns, or runs against non-loopback
endpoints.

## Inspect and stop

```
docker compose ps
docker compose logs --tail=100 dcss
docker compose port dcss 8080
curl --max-time 10 -fsS -o /dev/null http://localhost:8080/
```

Save your character normally before stopping the server. Quitting/abandoning a character is not the same as saving it.

```
docker compose stop
```

To remove the container and its network while retaining saved data:

```
docker compose down
```

Run the start command again to recreate the service. Automatic restart is disabled.

## Configuration and persistence

- Compose project name: `crawl-lab`. Keep it unchanged when moving the repository if you want to reuse the same data volume.
- Named volume: `crawl-lab_dcss-data`, mounted at `/data`.
- Accounts and settings: `/data/webserver`.
- Per-account RC files: `/data/rcs`.
- Character saves: `/data/saves/`, configured by the `CRAWL_DIR` environment variable in Compose.
- New-account RC template: `config/init.txt`, mounted read-only. Existing account RCs are not overwritten when this template changes.
- Important: this image uses a DGAMELAUNCH build that ignores `save_dir` in player RC files. Keep `CRAWL_DIR` configured; an RC setting alone does not make saves persistent.
- Locale: `LANG` and `LC_ALL` are set to `C.UTF-8`; Crawl's WebTiles binary requires a UTF-8 locale.

The only host bind mount is the read-only RC template. No host directory, Docker socket, host credential file, or API key is mounted. Accounts and gameplay data live in the Docker volume, not in the repository.

**Do not run `docker compose down -v` or remove the named volume unless intentionally deleting all local accounts and saves.** A Docker-managed volume is persistence, not a backup.

## Validation status

- Verified: AMD64 execution under Docker Desktop on Apple Silicon; healthy lobby with HTTP 200; Crawl reports version 0.34.0; loopback-only port publication.
- User-confirmed: account registration and a brief playable game.
- Verified during migration: a saved character was copied into the persistent volume with an exact SHA-256 match, and Crawl's `-save-json` command recognized the same character as loadable.
- Verified for milestone 1: a separate test account registered through WebTiles, created a Minotaur Berserker, saved, disconnected, resumed the same character, and saved again. The minimal preservation summary matched at turn 0; the pre-existing personal save's checksum was unchanged.
- Pending: confirm browser-based resumption of the original personal character after service migration. The separate test-account result does not establish that manual acceptance check.

The health check only tests the HTTP lobby, not WebSocket gameplay or character persistence.

## Repository scope

The repository contains the local server configuration, Python lifecycle client,
and tests. The working `PLAN.md`, local environment files, and runtime data are
excluded from Git. No TypeSafe API key is needed for this milestone.
