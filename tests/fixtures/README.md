# Lifecycle fixture

`lifecycle-0.34.0.json` is a reduced transcript captured from the pinned local
DCSS 0.34.0 WebTiles image during a dedicated test-account creation and save.

- Account identity is replaced with `TestPlayer`.
- Authentication, HTML, chat, descriptions, inventory, and tile payloads are omitted.
- Only the selected creation button is retained for each screen.
- Lifecycle event order and the initial placeholder player followed by partial
  player updates are preserved. A placeholder player is not a ready character.
- Empty map cells are intentional: these tests cover lifecycle, not M3 map reconstruction.

The transport supports the official `no-compression` subprotocol. Tests also
exercise bundled messages, malformed input, and a real loopback WebSocket server;
none of those tests needs Docker or an external service.
