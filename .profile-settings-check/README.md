# Profile settings verification

Run `node .profile-settings-check/server.cjs` from the repository, open
`http://127.0.0.1:34328`, and select **Run regression checks**.

This runs the edited website panel with the actual shared DuckyOS tabs and
sliding indicator source. Account data and submissions are simulated locally.
It checks Save placement and form submission, label association, photo input
activation and preview updates, labeled metadata, tab highlight alignment,
reopening, delayed profile rendering, and overflow. It also loads the hero script
and checks that only the flag remains in the header, Log out stays in General,
and the plus card remains visible and operable after PC list refreshes.

Verified at desktop width in dark mode and 360px width in light mode.
Published through the site connector as ai-uefn-profile 0.1.73; the subsequent
0.1.74 profile-header release retained these panel assets.

The browser available for this task was signed out of the production site, so
authenticated production interaction was not tested.
