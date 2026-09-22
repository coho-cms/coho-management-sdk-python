# `coho_management_sdk` — library reference

The Python management SDK for Coho. It is also the library under the
[`coho` CLI](https://github.com/coho-cms/coho-cli): everything that command does is one
call away here, with the same names, so CI scripts and notebooks import it without the
CLI.

```python
from coho_management_sdk import Coho

coho = Coho.from_profile("staging")          # or Coho(url=..., token=...)
site = coho.account("acme").project("0192…")
dev = site.ref("dev")

post = dev.entries.get("0192…")
post.fields["title"]["en-US"] = "New title"
dev.entries.put(post)                        # sends If-Match from post.etag
```

| Page | |
|---|---|
| [Getting started](getting-started.md) | constructing a client, profiles, tokens |
| [Object model](object-model.md) | `Coho` → `Account` → `Project` → `Ref`, every method |
| [Accounts and members](accounts.md) | `me`, members, invitations, entitlements, users |
| [Content](content.md) | types, entries, ETags, pagination, history, references |
| [Branches and releases](releases.md) | branches, diff, merge, tags, environments, promote, rollback, export, preview |
| [Errors](errors.md) | `CohoError`, the subclasses, branching on `code` |
| [Testing](testing.md) | `coho_management_sdk.testing.FakeBff` for your own tests |

Three rules:

- **Errors are the `code`.** Every failure raises `CohoError` with `.code`, `.status`
  and the problem's context fields; subclasses exist for the codes a caller branches on.
  Never match on status alone.
- **The ETag is carried by the object.** A `get` returns a value that remembers its
  ETag; a `put` of it sends `If-Match`. Overwrite blind with `force=True`.
- **Every model keeps `.raw`**: the response as the contract describes it.

The package is typed (`py.typed`); `mypy --strict` passes on it.

The contracts this version implements are vendored in
[`contracts/`](../contracts/README.md) at a pinned commit of the server repository. The
task-shaped guides — install, quickstart, authentication, configuration, concepts, the
content model, CI — live with the
[CLI](https://github.com/coho-cms/coho-cli/blob/main/docs/index.md).
