# Profile SPA regression

Run `node .profile-spa-check/server.mjs` from this workspace, then open
`http://127.0.0.1:34326/?script=fixed` and press **Run regression checks**.
Use `?script=original` to reproduce the previous failures.

This fixture executes the actual profile plugin script with simulated asynchronous
identity, language, security, and PC components. It never contacts authenticated
endpoints or submits account changes. It checks nine lifecycle cases: first visit,
return visit with persistent main, PC refresh, replacement PC component, identity
replacement, late security rendering, loading beyond three seconds, replacement
main, and observer cleanup on route exit.

The tested fix was published to UEFN Ducky as ai-uefn-profile 0.1.63 on 2026-09-16,
using assets/blocks/hero.d44.js.
