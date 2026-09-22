# Changelog

All notable changes to `coho-sdk`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — unreleased

Extracted from `coho-cli`, where it began life as a workspace package, into its own
repository and distribution.

### Added

- `Coho` → `Account` → `Project` → `Ref` object model over Coho's BFF: accounts,
  members, invitations, entitlements, projects, roles, refs, branches, tags, content
  types, entries, diff, merge, environments, tiers, delivery keys, export and preview.
- Problem documents mapped to `CohoError` subclasses by `code`, covering both the RFC
  9457 shape from the authoring tier and the bare `{code, detail}` from the auth tier.
- ETags carried by `Entry` and `ContentType`, so a `put` sends `If-Match`; a write with
  neither an ETag nor `force=True` is refused locally.
- Browser PKCE login with keyring and file token stores, silent refresh, and a
  `COHO_ACCESS_TOKEN` override.
- Profiles and sticky context in `~/.config/coho/config.toml`.
- `coho_sdk.testing.FakeBff`, a contract-shaped fake server, published under the
  `testing` extra so downstream projects can test against it.

### Fixed

- The file token store no longer calls `os.fchmod`, which does not exist on Windows.
  Owner-only permissions are applied where the platform has them and skipped where it
  does not.

[Unreleased]: https://github.com/coho-cms/coho-sdk-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/coho-cms/coho-sdk-python/releases/tag/v0.1.0
