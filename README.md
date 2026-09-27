# Crawl Lab

A lab for experimenting with AI decision backends that play Dungeon Crawl Stone Soup. Game connections are kept separate from decision policies; TypeSafe AI's Jev is the first planned experiment, not a permanent dependency of the architecture.

**Current milestone:** a local WebTiles server for manual play. The agent backend and Jev integration are not implemented yet.

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

For future programmatic clients, the WebTiles WebSocket endpoint is `ws://localhost:8080/socket`. No agent client is configured yet.

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
- Pending: confirm browser-based resumption after service migration. Do not infer actual gameplay resumption from a healthy lobby or save metadata alone.

The health check only tests the HTTP lobby, not WebSocket gameplay or character persistence.

## Repository scope

`compose.yaml`, `config/init.txt`, and this README are the current project artifacts. The working `PLAN.md`, local environment files, and runtime data are excluded from Git. No TypeSafe API key is needed for this milestone.
