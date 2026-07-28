# Security Exceptions

Relayna CI fails on high or critical dependency, filesystem, image, secret, and
static-analysis findings unless an exception is explicitly documented.

Document every entry added to `.trivyignore`, `.gitleaks.toml`, `.semgrep.yml`,
or a dependency-audit exception file here with:

- finding ID or rule name
- owner
- affected component
- reason
- tracking link
- expiration date

## Active Exceptions

### GHSA-qwww-vcr4-c8h2

- Owner: Relayna maintainers
- Affected component: Studio frontend `react-router` and `react-router-dom`
- Reason: The advisory affects only unstable React Server Components APIs.
  Relayna Studio is a client-only `BrowserRouter` application and does not use
  RSC APIs. `react-router-dom` has no compatible release using the patched
  `react-router` 8.3.0; that release also requires Node 22.22 or newer and
  newer React peer versions. The exception remains narrow: every other high or
  critical npm finding fails CI.
- Tracking link: https://github.com/sarattha/relayna/pull/111
- Expiration date: 2026-08-28
