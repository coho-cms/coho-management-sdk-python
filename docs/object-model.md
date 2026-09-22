# Object model

```
Coho
├── me() → Me                      accounts() → [Membership]        plans()
├── users: UsersApi                rename, logins, detach_login
├── signup_start(), invitation_lookup(), invitation_accept_start()
└── account(id_or_name) → Account
    ├── id, name, role, membership
    ├── rename(), entitlements(), events()
    ├── members: MembersApi        list, change_role, remove
    ├── invitations: InvitationsApi   list, create, revoke
    ├── projects: ProjectsApi      create(name) → Project, get(id) → Project
    └── project(id) → Project
        ├── id, name, info()
        ├── refs: RefsApi          list, get, describe
        ├── branches: BranchesApi  list, create
        ├── tags: TagsApi          list, create
        ├── diff(from, to), merge(source, target, …)
        ├── environments: EnvironmentsApi   list, get, create, promote, unset, delete, history, rollback
        ├── tiers: TiersApi        list, create, delete
        ├── roles: RolesApi        list, grant, revoke
        ├── delivery_keys: DeliveryKeysApi  list, issue, revoke, set_public
        ├── export(ref, …), preview(ref) → Preview
        └── ref(name) → Ref
            ├── info(), export(), preview()
            ├── types: TypesApi    list, get, put, delete
            └── entries: EntriesApi   list, iterate, get, create, put, delete, history, references
```

Each level owns the path segment it adds:

| Object | Path |
|---|---|
| `Account` | `/api/v1/accounts/{account}` |
| `Project` | `…/projects/{project}` |
| `Ref` | `…/refs/{ref}` (URL-encoded: `feature/x` → `feature%2Fx`) |
| `Preview` | `…/projects/{project}/preview/{ref}` |

Constructing `Account`, `Project` or `Ref` does **not** call the server; the first
method that needs data does. `Coho.account(name)` is the exception: a *name* is
resolved through `/api/v1/me` (cached per client), an id is used as-is.

## Models

Every model is a dataclass with snake_case attributes and a `.raw` dict holding the
response as received. The important ones:

| Model | Notable attributes |
|---|---|
| `Me` | `user_id`, `display_name`, `email`, `accounts: [Membership]`, `membership(id_or_name)` |
| `Membership` | `account_id`, `account_name`, `role` (`admin`/`member`), `actor_id` |
| `Member` | `id`, `display_name`, `email`, `role` |
| `Invitation`, `IssuedInvitation` | `…status`, `expires_at`; issued: `.token` (once), `.link(public_url)` |
| `Entitlements` | `plan`, `max_projects`, … — `None` means **unlimited** |
| `ProjectInfo` | `id`, `name`, `trunk`, `environments` |
| `RefInfo` | `name`, `kind`, `target`, `description` |
| `Branch` | `name`, `kind`, `status`, `parent`, `depth`, `branched_at`, `pending_forward_port` |
| `Tag` | `name`, `ref`, `at`, `warnings` |
| `ContentType` | `slug`, `version`, `definition`, `etag`, `.name`, `.fields` |
| `Entry` | `id`, `type`, `slug`, `version`, `fields`, `etag` |
| `EntryPage` | `entries`, `page.has_more`, `page.offset` |
| `History`, `HistoryEntry` | versions by `seq` desc; `op` is `upsert`/`tombstone` |
| `References` | `incoming: [IncomingReference]`, `total` |
| `Diff` | `added`, `modified`, `deleted`, `nodes`, `conflicts`, `merge_token`, `base` |
| `Conflict`, `Resolution` | resolution: `node_id`, `take`, `path`, `value` |
| `MergeResult` | `merged`, `unchanged`, `resolved` |
| `Environment` | `name`, `tier`, `resolves`, `target`, `previous_target`, `warnings` |
| `RefHistory` | `history: [RefHistoryEntry]` with `from_target`, `to_target`, `reason` |
| `Tier` | `id`, `resolves`, `ord`, `promote_permission`, `environments` |
| `ProjectRole` | `actor_id`, `role`, `granted_by`, `granted_at` |
| `DeliveryKey`, `IssuedDeliveryKey`, `DeliveryKeyList` | issued: `.key` (once); list: `keys`, `public_refs` |
| `Export` | `path`, `seq`, `bytes_written`, `redirected_to`, `manifest` |

Constants: `coho_sdk.models.PROJECT_ROLES`, `ACCOUNT_ROLES`.

## Transport

`coho_sdk.transport.Transport` is the one place that knows about `httpx`, the bearer
header, problem documents and ETags. `request(method, path, json_body=, params=,
headers=, if_match=, follow_redirects=, stream=)` returns a `Response(status, body,
etag, headers, raw)` or raises. `params` entries that are `None` are dropped. You
rarely need it directly; `coho.transport` is there for an endpoint the object model
does not cover yet.
