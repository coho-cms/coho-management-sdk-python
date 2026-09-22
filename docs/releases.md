# Branches and releases

```python
site = coho.account("acme").project(PROJECT)
```

## Refs

```python
site.refs.list()                        # [RefInfo]; kind: version_branch | feature_branch | tag | environment
site.refs.list(kind="tag")
site.refs.get("feature/pricing")        # slashes are encoded for you; RefNotFound with .ref
site.refs.describe("v1.0.0", "Release notes…")     # None or "" clears
```

## Branches

```python
site.branches.list()                    # [Branch] with depth, status, pending_forward_port
b = site.branches.create("feature/pricing", from_ref="v0.0.x", description="New pricing")
b.branched_at                           # the sequence; b.depth_warning
site.branches.create("v1.x", from_ref="v0.0.x", kind="version_branch")   # needs branch:create_version
site.branches.create("hotfix", from_ref="v0.0.x", at=1234)                # explicit sequence
```

## Diff and merge

```python
d = site.diff("v0.0.x", "feature/pricing")       # from=target side, to=branch under review
d.added, d.modified, d.deleted
for n in d.nodes:  n.slug, n.change, n.fields
d.conflicts                                      # [Conflict]: node_id, slug, reason, paths
d.merge_token                                    # round-trip as expected_token
site.diff("v0.0.x", "feature/pricing", mode="absolute")   # two-dot
```

```python
from coho_sdk import MergeConflict, Resolution

try:
    result = site.merge("feature/pricing", "v0.0.x", message="New pricing", expected_token=d.merge_token)
except MergeConflict as exc:
    for c in exc.conflicts:                       # dicts: nodeId, slug, reason, paths
        ...
    result = site.merge(
        "feature/pricing", "v0.0.x", message="New pricing",
        resolutions=[Resolution(node_id=c["nodeId"], path=c["paths"][0], take="source") for c in exc.conflicts],
        expected_token=d.merge_token,
    )
result.merged, result.unchanged, result.resolved
```

`take` is `"source"`, `"target"` or `"value"` (with `value=`, only with a `path`).
Omit `path` to settle the whole node. `MergeRefsMoved` if the refs changed since the
diff behind `expected_token`.

## Tags

```python
site.tags.list()                                   # refs of kind tag
t = site.tags.create("v1.4.0", branch="v0.0.x", description="Q3 catalogue")
t.at                                               # the frozen sequence
t.warnings                                         # e.g. PENDING_FORWARD_PORT
site.tags.create("v1.4.1", branch="v0.0.x", at=1234)
```

Only version branches; no delete.

## Environments and promotion

```python
site.environments.list()                           # [Environment]: name, tier, resolves, target
site.environments.get("prod")
site.environments.create("qa2", tier="qa", target=None)

env = site.environments.promote("qa", "v1.4.0")                    # waits on SNAPSHOT_NOT_READY
env.target, env.previous_target, env.warnings                      # SKIPPED_TIER, …
site.environments.promote("prod", "v1.4.0", expected_target="v1.3.0", reason="Go live")
site.environments.promote("qa", "v1.4.0", wait=False)              # raises SnapshotNotReady instead
site.environments.promote("qa", "v1.4.0", timeout=600, poll=5, on_wait=lambda exc, left: print(exc.context.get("state"), left))
site.environments.unset("qa")
site.environments.delete("qa2")
```

`expected_target` → `EnvironmentMoved` if the environment moved. Ordering is
advisory: promoting past a tier succeeds with a `SKIPPED_TIER` warning.

### Rollback

```python
env = site.environments.rollback("prod", reason="v1.4.0 broke checkout")
```

Reads the history, takes the previous target, and promotes to it with
`expected_target` set to the current one — so if somebody already fixed it forward,
this raises `EnvironmentMoved` rather than undoing their work. `CohoError` with code
`NO_PREVIOUS_TARGET` if there is nothing to go back to.

```python
h = site.environments.history("prod", limit=20)
for x in h.history:  x.from_target, x.to_target, x.reason, x.actor, x.at
```

## Tiers (owner)

```python
site.tiers.list()                                  # [Tier]: id, resolves, ord, promote_permission, environments
site.tiers.create("qa2", resolves="snapshot", ord=15)
site.tiers.delete("qa2")                           # TIER_NOT_EMPTY otherwise
```

## Roles (owner)

```python
site.roles.list()                                  # [ProjectRole]: actor_id, role, granted_by, granted_at
site.roles.grant(actor_id, "maintainer")           # idempotent PUT
site.roles.revoke(actor_id)
```

Actor ids come from `coho.me().accounts[i].actor_id` (your own) and, for others, from
whatever directory you keep — the content tier stores ids, never names.

## Delivery keys (owner)

```python
issued = site.delivery_keys.issue(label="site-prod", refs=["prod"], expires_in_days=365)
issued.key                                         # ⚠️ once
issued.delivery.id

ks = site.delivery_keys.list()                     # ks.keys (metadata), ks.public_refs
site.delivery_keys.revoke(key_id)
site.delivery_keys.set_public(["prod"])            # environments only; replaces the set
```

`refs=None` grants every ref; `refs=[]` is refused.

## Export

```python
result = site.export("v1.4.0")                                 # → ./v1.4.0.ndjson
result = site.export("feature/pricing", to="/tmp/branch.ndjson")
result.seq                                                     # the sequence a branch was resolved at
result.redirected_to                                           # the presigned URL, for a tag
with open("out.ndjson", "wb") as fh:
    site.export("v1.4.0", to=fh)
site.export("v1.4.0", format="tar", stream=True)               # bytes through the API, no redirect
```

## Preview

```python
p = site.preview("dev")                            # or site.ref("dev").preview()
p.entries(type="blogPost", locale="en-US", order="-publishedAt", limit=25)
p.entry(entry_id, locale="en-US")
```

Delivery-contract shapes, straight from authoring, through the BFF.
