# Contributing

## Setup

```bash
uv sync --all-extras       # the library, its extras, and the dev tools
uv run pytest              # 48 tests, no network, no Docker, no server
uv run ruff check . && uv run ruff format --check .
uv run mypy                # strict
```

Or `make check` for all of it. Everything CI runs, you can run locally.

## Layout

```
src/coho_management_sdk/
  errors.py        CohoError and the branchable subclasses; error_for_response()
  transport.py     httpx, the bearer header, problem+json → CohoError, ETag capture
  auth.py          PKCE login, token stores (keyring/file), refresh, TokenProvider
  profiles.py      ~/.config/coho/config.toml: profiles, context, project registry
  models.py        typed views over the contract's JSON (each keeps .raw)
  client.py        Coho → Account → Project → Ref, and the *Api classes
  testing.py       FakeBff: a contract-shaped fake, published for downstream tests
  _version.py      generated from git tags at build time; git-ignored
tests/             unit and contract tests against FakeBff
contracts/         bff.yaml, authoring.yaml, delivery.yaml, PIN (the server commit)
docs/              the library reference
```

## Rules of the road

1. **Branch on `code`, never on status.** Add a subclass in `errors.py` only for codes a
   caller would plausibly `except`; everything else is a plain `CohoError` whose `.code`
   is still exact.
2. **Every model keeps `.raw`.** Consumers print the response as the contract describes
   it, not as our dataclass happens to spell it.
3. **The ETag belongs to the object.** A read carries it; a write sends it. A write with
   no ETag and no `force=True` is refused locally, before a request the server would
   refuse anyway.
4. **Tests run against `FakeBff`**, never a live server. A behaviour that depends on a
   contract detail belongs in the fake too (`If-Match` → 428/412, and so on).
5. **Nothing POSIX-only without a fallback.** `restrict_to_owner` is the example: file
   modes where they exist, a no-op where they do not.

## Contracts

`contracts/` is copied from the server repository at the commit in `contracts/PIN`:

```bash
make contracts COHO_DATA=../coho-data
```

Then read the diff and update `models.py`, `client.py`, `testing.py` and `docs/` to
match. Bumping the pin is the release trigger.

## Versioning and releasing

The version comes from git tags, computed at build time by `hatch-vcs` (with
`setuptools-scm` underneath). Nothing in the source tree holds it; the build writes
`src/coho_management_sdk/_version.py`, which is git-ignored, and a test asserts it
agrees with the installed metadata.

| You build from | Version |
|---|---|
| a commit tagged `v0.2.0` | `0.2.0` |
| three commits after it | `0.2.1.dev3`: a dev build, Python's equivalent of a Maven SNAPSHOT |
| a commit tagged `v0.3.0rc1` | `0.3.0rc1` |
| a repository with no tags yet | `0.1.devN` |

Installers skip dev and pre-release versions unless a requirement names one, so
publishing them never surprises anybody: the CLI asks for `>=0.1.0.dev0` on purpose.

To release:

1. Add a `CHANGELOG.md` entry, and merge to `main`.
2. Tag the commit and push the tag: `git tag v0.2.0 && git push origin v0.2.0`. Use
   `vX.Y.ZaN`, `bN` or `rcN` for alphas, betas and release candidates; GitHub marks
   those as pre-releases.
3. CI tests, builds a wheel and an sdist, installs the wheel into a clean environment
   to check it imports, uploads both as a build artifact, and attaches them to a
   GitHub Release for the tag.

⚠️ **Never tag a dev version.** Dev versions come from commits after a release. A tag
like `v0.3.0.dev1` makes every later build fail, since a dev build of a dev build
cannot be numbered, so CI refuses one before building. It also refuses a tag that is
not in PEP 440's standard form, such as `v0.2.0-rc1`, which would build `0.2.0rc1`
and no longer match its own tag. If one slips through, delete it:
`git push --delete origin <tag>`.

Every run on `main` and every pull request also uploads the built distributions as an
artifact, so a dev build is downloadable without a release. CI fetches the whole history
(`fetch-depth: 0`); a shallow clone has no tags and would build a meaningless `0.1.dev1`.

### Publishing to PyPI as well

The workflow has a `publish-pypi` job that is skipped unless you ask for it. To enable:

1. Register this repository as a [trusted publisher](https://docs.pypi.org/trusted-publishers/)
   on PyPI for the `coho-management-sdk` project, with workflow `ci.yml` and environment `pypi`.
2. Create a GitHub environment named `pypi` (protect it with required reviewers).
3. Set the repository variable `PUBLISH_TO_PYPI` to `true`.

No API token is stored anywhere; the job authenticates with a short-lived OIDC token.

### Build provenance (optional)

To attach signed provenance to each release, add to the `build` job:

```yaml
    permissions:
      contents: read
      id-token: write
      attestations: write
    # after the build step:
      - uses: actions/attest-build-provenance@v2
        with:
          subject-path: dist/*
```

It needs a public repository (or GitHub Advanced Security), which is why it is not on
by default.
