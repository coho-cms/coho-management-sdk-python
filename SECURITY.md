# Security

## Supported versions

The latest released version. While the major version is `0`, fixes land on `main` and
in the next release rather than as patches to older ones.

## Reporting

Email the maintainers privately rather than opening an issue. We will acknowledge
within three working days.

## What this library holds

- **Access and refresh tokens** for the identity provider, per profile, in the OS
  keyring — or in `~/.config/coho/credentials.json` (mode 0600) when there is no
  keyring or the profile says `token_store = "file"`. `TokenProvider.forget()` — or
  `coho logout` — removes them.
- **Nothing else that is a credential.** The SDK is a bearer caller; the BFF chooses the
  actor. There is no `X-Coho-Actor`, no `externalId`, no client secret.

## Things that are shown once

`DeliveryKeysApi.issue()` and `InvitationsApi.create()` return a secret the server will
never show again — only its hash is stored. Do not log the object they return; take the
`.key` or `.token` and hand it straight to wherever it belongs. Deliver invitation links
over a channel that does not leak; `IssuedInvitation.link()` puts the token in the URL
fragment, which browsers never send to servers.

## `COHO_ACCESS_TOKEN`

Overrides the stored token, for every client in the process. It carries a person's identity and is a stopgap for CI
until service accounts exist. Scope it to the job, rotate it, and never log it.
