# Security policy

UEFN-Ducky stores no API keys, Stripe secrets, or JWT signing material in this
repository. Desktop login mints a `dky_v1_` device key at runtime and stores it
DPAPI-encrypted in `%LOCALAPPDATA%/UEFN-Ducky/credentials.dat`.

## Report a vulnerability

Email **security@uefnducky.org** or open a private GitHub security advisory on
this repository. Please do not file a public issue for credential leaks or
auth bypasses.

We aim to acknowledge reports within 72 hours.

## Unofficial tokens

There is no official UEFN Ducky or DuckyOS cryptocurrency. Coins on pump.fun,
bump.fun, or similar that use our name, logo, GitHub, or contributor list are
unofficial. This project did not launch them and does not claim creator rewards.
A GitHub profile appearing on a coin is not affiliation.

## What we will not treat as a vulnerability

- Client-side Store UI gates (`needsPurchase`) that the server already enforces
  on download.
- Running a paid plugin zip after a legitimate purchase (licensing is at
  download time, not DRM).
- Open-sourcing this app. Secrets live on the server and in per-user DPAPI
  storage, not in the source.

## Unofficial tokens

There is no official UEFN Ducky or DuckyOS cryptocurrency. The Contributors graph does not
authorize fee claims. See [UNOFFICIAL_TOKENS.md](UNOFFICIAL_TOKENS.md).
