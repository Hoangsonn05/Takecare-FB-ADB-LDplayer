# Changelog

## v3

- Removed the standalone Validate Paths button from GUI.
- Facebook package is fixed to `com.facebook.katana`.
- Added robust LDPlayer base auto-resolution.
- Manual LD IDs can start even when `ldconsole list2` returns no instances.
- Worker now logs precise path, launch, ADB and app-opening stages.
- Added ADB monkey fallback for opening Facebook when `ldconsole runapp` fails.

## v2

- Added hardcoded Facebook package.
- Added LD IDs input and Start IDs.
- Added basic already-running check.

## v1

- Initial modular GUI/core/scheduler project.
