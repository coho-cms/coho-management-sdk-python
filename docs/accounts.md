# Accounts and members

## Who am I

```python
me = coho.me()                 # cached; me(refresh=True) to re-read
me.user_id, me.display_name, me.email
for m in me.accounts:
    m.account_id, m.account_name, m.role, m.actor_id
```

`me.membership("acme")` finds by id or name. Membership is read fresh by the server
on every call, so a removal or role change shows on the next `me(refresh=True)`.

## An account

```python
acme = coho.account("acme")            # name (case-insensitive) or id
acme.id, acme.name, acme.role          # role: "admin" | "member"
acme.rename("Acme Ltd")                # admin
acme.entitlements()                    # admin; Entitlements, None limit = unlimited
acme.events(limit=100)                 # admin; [AuditEvent], newest first
coho.plans(include_retired=False)      # [Plan]
```

## Members (account-level roles)

```python
for u in acme.members.list():          # admin
    u.id, u.email, u.role
acme.members.change_role(user_id, "admin")   # LastAdmin if it would leave none
acme.members.remove(user_id)                 # or your own id, to leave
```

Needs the `coho-auth/accounts` scope on the token (leaving needs only `self`);
`InsufficientScope` otherwise. `AccountAdminRequired` when you are a member, not admin.

## Invitations

```python
issued = acme.invitations.create("jane@acme.example", role="member")
issued.token                           # ⚠️ shown once; only its hash is stored
issued.link("https://staging.coho.example")   # …/invite#<token> — token in the fragment
issued.invitation.expires_at

acme.invitations.list()                # [Invitation] with .status
acme.invitations.revoke(invitation_id) # InvitationUnusable if already accepted/…

coho.invitation_lookup(token)          # no login needed → Invitation
coho.invitation_accept_start(token, display_name="Jane")   # → a URL to open; finishes in a browser
coho.signup_start("Acme")              # likewise
```

Coho does not send email. Deliver the link over a channel that does not leak.

## Users (yourself)

```python
coho.users.rename(me.user_id, "Ada L.")        # → Me
coho.users.logins(me.user_id)                   # [Login]: id, provider, subject
coho.users.detach_login(me.user_id, login_id)   # LastLogin for your only one
```

## Projects

```python
site = acme.projects.create("Marketing site")   # → Project, bootstrapped
site = acme.projects.get(project_id)            # fetches, NotFound if you cannot see it
site = acme.project(project_id)                 # lazy; no request yet
site.info().trunk                               # "v0.0.x"
```

⚠️ No listing: the server cannot list a caller's projects yet. Keep ids in your own
config, or use `Profile.remember_project` / `resolve_project` for the CLI's registry.
