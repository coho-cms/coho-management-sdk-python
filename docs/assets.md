# Assets

Files on a ref: images, PDF, audio and video (doc 40 in coho-data). An asset is a node
like an entry, so it branches, merges and promotes. Its bytes, addressed by their
SHA-256, do not branch at all, so a branch with gigabytes of media costs nothing.

```python
dev = coho.account("acme").project("0192…").ref("dev")

hero = dev.assets.upload("photos/hero shot.jpg", slug="hero-shot", title="Hero",
                         alt="The bridge at dusk", focal_point=FocalPoint(0.5, 0.4))
hero.url          # the original, from the asset CDN
hero.image_url    # raster images only: add ?w=800&fm=auto to resize on request
```

## ⚠️ Assets are public by URL

Every asset is public to anyone holding its URL, in every project, keyed or not.

- **Drafts are included.** An asset only a draft branch references is as private as its
  URL, and URLs leak through preview links, screenshots and browser history. A leaked
  URL cannot be revoked.
- **Deleting does not stop the URL working.** History and released tags still point at
  the bytes, so a rollback finds its images.
- **Someone holding a file can tell whether a project has it.** The URL is the file's
  hash, so it can be computed from the file.

The URL cannot be guessed: it carries a 256-bit hash. Private assets on signed URLs are
planned as a per-project option.

## Uploading

`upload` hashes the file (streaming it, never reading it whole), then makes three
requests:

1. `begin_upload(sha256=, byte_size=, filename=, content_type=)` returns an
   `AssetUpload`. Its `put` is a presigned PUT with headers that are part of its
   signature. `put` is `None` when the project already holds these bytes, and the next
   step is skipped.
2. `put_bytes(upload, source, length=)` sends the bytes straight to object storage,
   never through the BFF, and without the BFF's token. The store refuses bytes whose
   length or hash differ from step 1, raised as `UploadRejected` with its
   `storage_code`.
3. `complete_upload(upload.id, slug=, title=, alt=, focal_point=, tags=)` has the server
   check what arrived, decide what it is from the bytes (never the filename), and write
   the asset. Completing again returns the same asset.

The steps are public so a caller can run them itself, for example to PUT from another
process. An upload must be completed within an hour (`AssetUploadExpired`).

`AssetsApi.MAX_BYTES` is the per-file limit, 1 GiB. `upload` refuses a larger file with
`AssetTooLarge` before sending anything.

## Reading, editing, deleting

```python
page = dev.assets.list(limit=50)          # slug order; page.page.has_more
for asset in dev.assets.iterate():
    ...

hero = dev.assets.get(hero.id)
hero.alt = "The bridge at night"
dev.assets.put(hero)                      # If-Match from hero.etag
dev.assets.put(hero.id, title="Hero", force=True)
dev.assets.delete(hero)
```

`put` replaces the metadata (`title`, `alt`, `focal_point`, `tags`) as a whole; the file
itself is never editable. Given an `Asset`, its own values are sent unless overridden.
Assets from `list` carry an ETag built from their version, so they can be put directly.
