# Changelog

All notable changes to `coho-management-sdk`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- `tiers.move(tier_id, ord)`: moves a tier in the pipeline, shifting the tiers it passes by
  one (coho-data doc 11, M6-15). Needs a server with `PATCH …/tiers/{id}`.
- `ContentType.kind`: `content`, or `component` for a type whose documents are repeater
  items (coho-data doc 40 §14.10). A type from a server without component types reads as
  `content`.
- Traits (coho-data doc 05 §10): `ref.traits` lists, reads, writes and deletes them, and
  `Trait` is one, a named group of fields types take. A trait's `put` is refused as
  `DestructiveSchemaChange` when it would cost a type that takes it content, unless
  `confirm_destructive`; its delete is refused as `TraitInUse` while any type takes it.
- Type inheritance on `ContentType`: `extends`, `abstract`, and `traits`, the traits it
  takes itself. `types.get` and `types.list` take `effective=True` for every field a type
  has, inherited and from traits, each with `from`.
- `FieldInherited`, `FieldConflict`, `TypeAbstract` and `TraitInUse` for those refusals, and
  `affectedTypes` in an error's `context`: the types a refused change reaches.

### Changed

- `tiers.create` inserts: an `ord` another tier holds moves that tier and the ones after it
  along, where it was refused with `TIER_ORDER_TAKEN`, and one past the end is the end
  (coho-data doc 11, M6-15). Positions always run 0 to n-1.
- After a browser sign-in, the loopback listener sends the browser on to the sign-in
  service's own "Signed in" page (`/cli/signed-in`, or `/cli/sign-in-failed`) instead of
  drawing an unstyled page itself, so the last page matches the rest. The code stays with
  the listener. Needs a sign-in service that serves those pages.

### Added

- Usage reports (coho-data doc 41): `Project.usage()` for anyone who can read the
  project, project tokens included, and `Account.usage()` for the account's admins,
  every project summed with each project's totals. Both take `grain`, `start`, `end`
  and `meters`. `UsageReport` totals a meter the way its kind says (`total`,
  `per_bucket`, `totals_by`), and `delivery_by_key()` gives each key's requests, 304s,
  refusals, bytes and requests per distinct URL: the client that is not caching. Models
  `UsageReport`, `AccountUsageReport`, `UsageMeter`, `UsageSeries`,
  `ProjectUsageTotals`, `KeyUsage`. `FakeBff` answers both routes from
  `testing.usage_report()`. Needs a server with the usage routes (coho-data 70ae1a4).
- Assets: `ref.assets` (`AssetsApi`). `upload(path, slug=…)` hashes a file, starts an
  upload, PUTs the bytes straight to object storage on the presigned URL (skipped when
  the project already holds them) and completes it. Also `list`, `iterate`, `get`,
  `put` (metadata, with `If-Match`), `delete`, and the three upload steps on their own.
  Models `Asset`, `AssetFile`, `AssetPage`, `AssetUpload`, `PresignedPut`, `FocalPoint`;
  `sha256_of`. Errors `UploadRejected` (the store's refusal, with `storage_code`),
  `AssetsUnavailable`, `AssetTooLarge`, `AssetTypeNotAllowed`, `AssetUploadIncomplete`
  and `AssetUploadExpired`. `FakeBff` encodes the routes and a fake object store. Needs
  a server with the asset routes (coho-data doc 40). ⚠️ Asset URLs are public to anyone
  holding them: see `docs/assets.md`.
- On PyPI: `pip install coho-management-sdk`.
- `Coho.login_url()`: the BFF's sign-in page, for a person to open in a browser, as
  `signup_url()` is for sign-up. Makes no request.
- `FakeBff.tokens`: the bearer tokens its `/me` answers for, so a test can stand for an
  identity that finishes setting up mid-test.
- `AccountNotFound`, a `NotFound` for the BFF's `ACCOUNT_NOT_FOUND`: an account that does not
  exist, or one the caller is not in. `FakeBff` answers it for everything under
  `testing.GONE_ACCOUNT`.
- `tags.create(..., validate=True)` asks the server to check every entry at the tag point
  before cutting the tag, and sends `validate` only then. A failure raises
  `TagContentInvalid` (`TAG_CONTENT_INVALID`): `entry_count` is how many entries fail,
  `invalid_entries` the first 100 by slug with their errors, and nothing is tagged.
  Needs a server that accepts `validate`.
- `FakeBff.invalid_entries`: what a tag cut with `validate` finds wrong. Empty, the
  default, means valid content.

### Changed

- The version comes from git tags at build time, through `hatch-vcs`, instead of a
  hand-edited `_version.py`. Tag `v0.2.0` builds 0.2.0; commits after it build dev
  versions such as `0.2.1.dev3`. CI refuses dev-version tags, marks alpha, beta and
  release-candidate tags as GitHub pre-releases, and fetches full history so tags are
  visible.

- `Coho.signup_url()` replaces `signup_start()`. Sign-up is now a page to open in a
  browser, `GET /auth/signup` on the BFF, and the SDK makes no request for it: it
  must stay an interactive session, where a captcha will be verified before release.
- `login()` sends `redirect_uri=http://localhost:<port>/callback` instead of
  `127.0.0.1`, since Cognito allows plain http for `localhost` alone. The listener
  answers on `127.0.0.1` and, where the machine has it, `::1`, since browsers
  resolve `localhost` to either.

### Added

- `Transport.request(..., auth=False)` for calls that must not carry a login.
- `coho_management_sdk.testing` fakes the invitation lookup and acceptance routes,
  with `INVITATION_TOKEN` as the one token they recognize.

### Fixed

- Invitation lookup and acceptance failed with `NOT_LOGGED_IN` for anyone without a
  stored login, who are exactly the people they exist for. Every request asked the
  token provider first; these now send no credential at all.
- The Windows type check failed on `os.fchmod`, which kept CI red and meant no
  build artifact was ever produced. `restrict_to_owner` now uses a `sys.platform`
  check that type checkers understand.
- The fake BFF's `preview/dev/entries` answered with authoring's list shape
  (`entries` as a list, plus `page`). It now answers with delivery's `EntryGraph`
  (`roots`, `entries` keyed by id, `unresolved`, `hasMore`), as the real endpoint does.

## [0.1.0] — unreleased

Extracted from `coho-cli`, where it began life as a workspace package, into its own
repository and distribution.

Published as **`coho-management-sdk`**, importable as `coho_management_sdk`. The name
says which half of Coho it speaks to: a separate `coho-delivery-sdk` will carry the
read-only client that a customer's website installs with a project key.

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
- `coho_management_sdk.testing.FakeBff`, a contract-shaped fake server, published under the
  `testing` extra so downstream projects can test against it.

### Fixed

- The file token store no longer calls `os.fchmod`, which does not exist on Windows.
  Owner-only permissions are applied where the platform has them and skipped where it
  does not.

[Unreleased]: https://github.com/coho-cms/coho-management-sdk-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/coho-cms/coho-management-sdk-python/releases/tag/v0.1.0
