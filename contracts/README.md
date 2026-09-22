# Vendored contracts

Copied from `coho-data` at the commit in `PIN`. These are what `coho_management_sdk` implements:

| File | Implemented by | Notes |
|---|---|---|
| `bff.yaml` | `client.py` (paths), `auth.py` (bearer) | The account segment, the login flow, which tier answers each path |
| `authoring.yaml` | `client.py` (`Project`, `Ref`, `*Api`), `models.py` | Byte-for-byte the shapes under `/api/v1/accounts/{account}/projects/…` |
| `delivery.yaml` | `client.py` (`Preview`) | The preview route serves this contract live |

The `:api-auth` tier has no OpenAPI document; its shapes are described in
`docs/accounts.md` and encoded in `models.py` and `testing.py`.

Bump with `make contracts COHO_DATA=../coho-data`.
