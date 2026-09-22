# coho-management-sdk

[![ci](https://github.com/coho-cms/coho-management-sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/coho-cms/coho-management-sdk-python/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/coho-cms/coho-management-sdk-python)
[![licence](https://img.shields.io/badge/licence-Apache--2.0-blue)](https://github.com/coho-cms/coho-management-sdk-python/blob/main/LICENSE)

The Python **management** SDK for Coho: accounts, projects, branches, content,
releases and delivery keys. It is the library under the
[`coho` CLI](https://github.com/coho-cms/coho-cli), and what CI scripts and notebooks
import directly.

> **Management, not delivery.** This is what authors, release managers and CI use to
> move content through the pipeline; it speaks to the authoring side with a person's
> credential. A website that only needs to *read* published content wants the delivery
> SDK instead, which takes a project's delivery key and cannot promote anything.
> Shipping one package that knows how to promote to production to every website is
> exactly what these two names exist to prevent.

```python
from coho_management_sdk import Coho

coho = Coho.from_profile("staging")            # or Coho(url=..., token=...)
site = coho.account("acme").project("0192…")
dev = site.ref("dev")

post = dev.entries.get("0192…")
post.fields["title"]["en-US"] = "New title"
dev.entries.put(post)                          # sends If-Match from post.etag

site.tags.create("v1.4.0", branch="v0.0.x", description="Q3 catalogue")
site.environments.promote("qa", "v1.4.0", wait=True)
```

## Install

```bash
pip install coho-management-sdk
pip install 'coho-management-sdk[keyring]'   # read tokens the CLI put in the OS keyring
# coho_management_sdk.testing.FakeBff, for your own tests:
pip install 'coho-management-sdk[testing]'
```

Python 3.11+. Synchronous, built on [httpx](https://www.python-httpx.org/). Typed, and
`mypy --strict` passes on it.

## Three rules

- **Errors are the `code`.** Every failure raises `CohoError` with `.code`, `.status`
  and the problem's context fields. Subclasses exist for the codes a caller branches on
  (`VersionConflict`, `MergeConflict`, `SnapshotNotReady`, `PlanLimit`, …). Never match
  on status alone: the same code arrives with different statuses from different tiers.
- **The ETag is carried by the object.** A `get` returns a value that remembers its
  ETag; a `put` of it sends `If-Match`. A caller who wants to overwrite blind says
  `force=True` and gets a refusal otherwise.
- **Every model keeps `.raw`** — the response exactly as the contract describes it, so
  nothing is lost between the wire and you.

## Documentation

| | |
|---|---|
| [Getting started](https://github.com/coho-cms/coho-management-sdk-python/blob/main/docs/getting-started.md) | constructing a client, profiles, tokens |
| [Object model](https://github.com/coho-cms/coho-management-sdk-python/blob/main/docs/object-model.md) | `Coho` → `Account` → `Project` → `Ref`, every method |
| [Accounts and members](https://github.com/coho-cms/coho-management-sdk-python/blob/main/docs/accounts.md) | `me`, members, invitations, entitlements |
| [Content](https://github.com/coho-cms/coho-management-sdk-python/blob/main/docs/content.md) | types, entries, ETags, pagination, history |
| [Branches and releases](https://github.com/coho-cms/coho-management-sdk-python/blob/main/docs/releases.md) | diff, merge, tags, promotion, rollback, export |
| [Errors](https://github.com/coho-cms/coho-management-sdk-python/blob/main/docs/errors.md) | the class per code, and what each means |
| [Testing](https://github.com/coho-cms/coho-management-sdk-python/blob/main/docs/testing.md) | `FakeBff`, a contract-shaped fake server |

## What it talks to

Coho's **BFF**, as a bearer caller, exactly as a browser session does. The SDK never
sees an authoring credential: it holds no `X-Coho-Actor`, no `externalId` and no client
secret, and the BFF chooses the actor for the account in the URL. That is what makes
this repository safe to publish.

The contracts it implements are vendored in
[`contracts/`](https://github.com/coho-cms/coho-management-sdk-python/tree/main/contracts) at a
pinned commit of the server repository.

## Related

- [coho-cli](https://github.com/coho-cms/coho-cli) — the `coho` command, a thin layer
  over this library, with the task-shaped guides (install, quickstart, authentication,
  configuration, concepts, content model, CI).
- `coho-delivery-sdk` — planned: the read-only client a customer's site installs, over
  the delivery contract, with a project key rather than a person's token.

## Licence

Apache-2.0. See [LICENSE](https://github.com/coho-cms/coho-management-sdk-python/blob/main/LICENSE).
