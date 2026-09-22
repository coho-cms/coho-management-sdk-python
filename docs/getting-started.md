# Getting started

```bash
pip install coho-sdk              # httpx, tomli-w
pip install 'coho-sdk[keyring]'   # to read tokens the CLI stored in the OS keyring
```

Python ≥ 3.11. Synchronous, built on `httpx`.

## Constructing a client

### From a profile the CLI configured

```python
from coho_sdk import Coho

coho = Coho.from_profile("staging")   # name, or $COHO_PROFILE, or the current profile
```

Reads `~/.config/coho/config.toml`, uses the profile's token store (keyring or file),
refreshes the access token when it is within a minute of expiry, and stores the new
one. `COHO_ACCESS_TOKEN`, if set, wins.

### With a token

```python
coho = Coho(url="https://staging.coho.example", token=os.environ["TOKEN"])
```

No refresh: the token is used as-is until it stops working (`Unauthenticated`).

### With your own token source

```python
coho = Coho(url=..., token_provider=lambda: vault.read("coho/token"))
```

Called before every request.

### Options

| Argument | |
|---|---|
| `url` | the BFF base URL |
| `token` | a bearer token, used as-is |
| `token_provider` | `Callable[[], str | None]` |
| `profile` | a `Profile`, for its token store and refresh settings |
| `timeout` | seconds per request, default 30; `None` for none |
| `http` | an `httpx.Client` to use (tests, proxies, custom TLS) |
| `transport` | a prebuilt `coho_sdk.transport.Transport` |

`Coho` is a context manager; `close()` closes the HTTP client.

## Logging in programmatically

```python
from coho_sdk import Config, login
from coho_sdk.auth import token_store_for

config = Config.load()
profile = config.profile("staging")
tokens = login(profile, timeout=300)          # opens the browser, runs PKCE
token_store_for(profile).save(profile.name, tokens)
```

`login(profile, open_browser=…, on_url=…, port=…, http=…)` — see `coho_sdk.auth`.
It needs `profile.oidc_domain` and `profile.client_id`; see
[authentication](https://github.com/coho-cms/coho-cli/blob/main/docs/authentication.md) for the app client this requires.

## Profiles and context from Python

```python
from coho_sdk import Config

config = Config.load()                     # or Config.load(path)
p = config.profile("staging", create=True)
p.url = "https://staging.coho.example"
p.context.account = "0192…"
p.remember_project("0192…", "Marketing site", "0193…")
config.current_profile = "staging"
config.save()                              # atomic, mode 0600
```

`Profile.resolve_project(account_id, name_or_id)` turns a registry name into an id.
