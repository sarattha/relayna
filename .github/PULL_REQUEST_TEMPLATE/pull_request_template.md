## Summary

<!-- Briefly describe what changed and why. -->

## Type of change

<!-- Mark the relevant items with "x". -->

- [ ] Feature
- [ ] Bug fix
- [ ] Refactor
- [ ] Tests
- [ ] Documentation
- [ ] Tooling / CI
- [ ] Release / packaging

## Affected areas

<!-- Mark the relevant items with "x". -->

- [ ] SDK (`src/relayna`, `tests`)
- [ ] Studio backend (`studio/backend`)
- [ ] Studio frontend (`apps/studio`)
- [ ] Docs (`docs`, README, changelog)
- [ ] Build, packaging, or CI

## Compatibility and migration notes

<!--
Call out changes to public imports, task/status/workflow contracts, route
responses, persisted Redis data, RabbitMQ topology or routing, serialized state,
environment variables, or other user-facing behavior.
-->

- Compatibility impact: <!-- None / additive / migration required / breaking -->
- Migration required: <!-- No / Yes, describe below -->

## Test plan

<!-- List the commands run and any manual verification. -->

- [ ] `make format`
- [ ] `make lint`
- [ ] `make typecheck`
- [ ] `make test`
- [ ] `make -C studio/backend format`
- [ ] `make -C studio/backend lint`
- [ ] `make -C studio/backend typecheck`
- [ ] `make -C studio/backend test`
- [ ] `make -C apps/studio test`
- [ ] `make -C apps/studio build`
- [ ] Other:

## Reviewer notes

<!-- Mention focused review areas, known limitations, follow-ups, or risks. -->

## Linked issues

<!-- Example: Fixes #123 -->
