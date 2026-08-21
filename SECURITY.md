# Security policy

UEFN-Ducky stores no API keys, Stripe secrets, or JWT signing material in this
repository. Desktop login mints a `dky_v1_` device key at runtime and stores it
DPAPI-encrypted in `%LOCALAPPDATA%/UEFN-Ducky/credentials.dat`.

## Report a vulnerability

Email **security@uefnducky.org** or open a private GitHub security advisory on
this repository. Please do not file a public issue for credential leaks or
auth bypasses.

We aim to acknowledge reports within 72 hours.

## What we will not treat as a vulnerability

- Client-side Store UI gates (`needsPurchase`) that the server already enforces
  on download.
- Running a paid plugin zip after a legitimate purchase (licensing is at
  download time, not DRM).
- Open-sourcing this app. Secrets live on the server and in per-user DPAPI
  storage, not in the source.
