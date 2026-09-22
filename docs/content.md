# Content

Content lives on a **ref**. Reads work on any ref; writes on branches only
(`BRANCH_NOT_WRITABLE` otherwise).

```python
dev = coho.account("acme").project(PROJECT).ref("dev")
```

## Types

```python
for t in dev.types.list():
    t.slug, t.name, t.fields, t.version

t = dev.types.get("blogPost")           # t.etag set
t.definition["fields"].append({"id": "subtitle", "type": "text"})
dev.types.put("blogPost", t.definition, if_match=t.etag)

dev.types.put("teamMember", {"_name": "Team member", "fields": [...]})   # create: no ETag needed
dev.types.put("blogPost", definition, force=True)                        # read current ETag, overwrite
dev.types.put("blogPost", definition, if_match=..., confirm_destructive=True)
dev.types.delete("blogPost", confirm_destructive=True)                   # else TypeInUse
```

`put` on an existing type without `if_match` or `force` raises `PreconditionRequired`
**locally**, before any request. Definitions are documented in
[content model](https://github.com/coho-cms/coho-cli/blob/main/docs/content-model.md).

## Entries

```python
page = dev.entries.list(type="blogPost", limit=100, offset=0)
page.entries, page.page.has_more, page.page.offset

for e in dev.entries.iterate(type="blogPost", page_size=200):   # every page
    ...

e = dev.entries.create(type="blogPost", slug="hello", fields={"title": {"en-US": "Hi"}})
e.id, e.etag
```

### Updating: the ETag travels with the object

```python
post = dev.entries.get(entry_id)          # post.etag == '"<version id>"'
post.fields["title"]["en-US"] = "New"
post = dev.entries.put(post)              # If-Match: post.etag; returns the new version

dev.entries.put(post)                     # again with the stale object → VersionConflict
```

Other forms:

```python
dev.entries.put(entry_id, fields, if_match=etag)     # you hold the ETag
dev.entries.put(entry_id, fields, force=True)        # read the current ETag, then write
dev.entries.put(entry_id, fields)                    # PreconditionRequired, raised locally
```

Handle a race:

```python
from coho_sdk import VersionConflict

for _ in range(3):
    post = dev.entries.get(entry_id)
    post.fields["rank"] = post.fields.get("rank", 0) + 1
    try:
        dev.entries.put(post)
        break
    except VersionConflict:
        continue
```

### Deleting

```python
tombstone_etag = dev.entries.delete(post)                    # If-Match from post.etag
dev.entries.delete(entry_id, if_match=etag)
dev.entries.delete(entry_id, force=True)
```

A delete is a tombstone: a new version with its own ETag.

### History and references

```python
h = dev.entries.history(entry_id, limit=50)
for v in h.versions:                    # seq descending; tombstones included
    v.seq, v.op, v.version, v.branch, v.actor, v.at

r = dev.entries.references(entry_id)    # complete, never paginated
r.total, [(i.node_id, i.field) for i in r.incoming]
```

## Validation errors

```python
from coho_sdk import ValidationFailed

try:
    dev.entries.create(type="blogPost", slug="x", fields={})
except ValidationFailed as exc:
    for err in exc.errors:              # [{"path": "title", "message": "required"}]
        print(err["path"], err["message"])
```

`SlugConflict` carries `context["slug"]` and `context["ref"]`.
