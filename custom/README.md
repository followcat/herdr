# Followcat Herdr

This distribution starts from upstream `herdrdev/herdr` v0.9.5 (`2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`). The `followcat` branch contains the custom changes; `master` remains an upstream reference. Release metadata is recorded in [release.json](release.json).

The existing terminal, workspace, session, socket and configuration locations remain those of Herdr. Installing a binary does not restart servers or panes. Existing personal settings are retained.

## Custom behavior

- Ctrl+G locks/unlocks Herdr shortcuts so input can pass through to the terminal.
- Floating terminals support selecting and copying text.
- Clipboard image pastes work with SSH clients and Kitty press/release events.
- Devin detection supports both interrupt hints, Thinking, long queued messages and question dialogs.
- Custom builds use this fork's binary feed, including SSH remote installation. Agent rules are pinned to the binary's source commit. They do not download official binaries through `herdr update`.

These changes do not alter the upstream private protocol, endpoint generation or persisted session schema. Personal themes, bindings and sidebar layouts remain configuration rather than compiled behavior; a sanitized example is in [config.example.toml](config.example.toml).

## Install and update

The initial release publishes a statically linked Linux x86_64 binary, usable on the existing Linux machines. Python 3.6+ is required for the standalone installer. Other platform artifacts can be added when needed.

```sh
curl -fL https://github.com/followcat/herdr/releases/latest/download/install.py -o /tmp/herdr-install.py
python3 /tmp/herdr-install.py install
herdr --version
```

The installer validates the release SHA-256 and the binary's reported build identity before replacing `~/.local/bin/herdr`. It preserves the previous binary under `~/.local/state/herdr-custom/backups/` and records the installation there. It never changes `~/.config/herdr/`, `~/.local/state/herdr/`, saved machines, running processes or session data.

Installation also creates the `herdr-followcat` command. Use it for later updates with a managed rollback record:

```sh
herdr-followcat update
herdr-followcat status
herdr-followcat rollback
```

Rollback refuses to overwrite a binary changed by another installer. Native `herdr update` also points at this fork, but follows the upstream updater's replacement behavior and does not maintain the standalone installer's rollback record. Use `herdr-followcat update` when installer-managed rollback is wanted.

Clients and servers are separate processes. New clients can reconnect to compatible old servers. Plan any server restart separately after its tasks finish; this distribution's installer does not stop them. An existing agent-manifest override continues to take precedence until it is explicitly retired. The original t14 Devin override should remain until the running server is verified to contain the bundled replacement.

## Build locally

Use the Rust version in `rust-toolchain.toml` and Zig 0.16.0, as required by the upstream build. The wrapper sets a custom build channel and ID without changing Cargo's upstream base version or protocol fields.

```sh
python3 scripts/custom.py build
target/release/herdr --version
```

To reproduce the portable Linux release, install Rust's `x86_64-unknown-linux-musl` target and musl build tools, then run:

```sh
python3 scripts/custom.py build --target x86_64-unknown-linux-musl
python3 scripts/custom.py package --binary target/x86_64-unknown-linux-musl/release/herdr
```

## Follow upstream

Every Monday at 03:17 UTC, `followcat-sync.yml` checks the latest upstream stable release. It can also be run manually from Actions. A newer release is merged on a separate `sync/upstream-*` branch and proposed as a PR against `followcat`. It does not automatically merge, publish or install anything. A conflicting merge is aborted and listed in the failed job for manual resolution.

The repository must allow Actions to create pull requests. The synchronization workflow explicitly dispatches checks for its new branch because ordinary workflows for bot-created PRs may wait for approval.

```sh
python3 scripts/sync_custom_upstream.py --check
```

Review upstream behavior and the custom patches before merging a sync PR. Remove custom changes when upstream has a verified replacement. Keep agent-specific screen validation as live smoke evidence rather than committed screen-classification unit tests.

## Publish a custom version

Update `custom/release.json` with a unique `build_id` and notes. Record the tested upstream tag and commit. The build ID is never reused, including across upstream versions. `custom.py package` preserves archived exact-build metadata from the previous update manifest so older clients can find matching remote artifacts.

```sh
python3 -c 'import sys; sys.path.insert(0, "scripts"); import custom; print(custom.source_metadata()["tag"])'
```

After the branch checks pass, create and push the printed tag. Only `followcat-v*` tags publish a release, and the workflow verifies that the tag matches `release.json`. The release includes the binary, standalone installer, exact-build update manifest and SHA256SUMS. Normal branch pushes build artifacts but do not publish a release.

Upstream workflows remain unchanged and retain their upstream-only publishing guards. Custom release and synchronization workflows are restricted to `followcat/herdr`.
