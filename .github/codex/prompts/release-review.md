# Release readiness review

You are Codex running in CI. Produce a release readiness report for the Relayna
repository.

Steps:

1. Determine the latest release tag using local tags only:

       git tag -l 'v*' --sort=-v:refname | head -n1

2. Set `TARGET` to the current commit SHA:

       git rev-parse HEAD

3. Collect diff context for `BASE_TAG...TARGET`:

       git diff --stat BASE_TAG...TARGET
       git diff --dirstat=files,0 BASE_TAG...TARGET
       git diff --name-status BASE_TAG...TARGET
       git log --oneline --reverse BASE_TAG..TARGET

4. Review release risk across these surfaces:

   - SDK public imports and documented behavior
   - task/status/workflow contracts
   - RabbitMQ topology, routing keys, retry behavior, and DLQ behavior
   - Redis keys, value shapes, TTLs, retention, and pubsub behavior
   - FastAPI route response shapes and status codes
   - Studio backend API behavior and event ingestion
   - Studio frontend behavior and build output
   - observability metrics, logs, execution graph payloads, and cardinality
   - packaging metadata, dependency changes, and CI/release workflows
   - docs, README, changelog, and migration guidance

Output:

- Write a concise Markdown report.
- Include the compare URL:
  `https://github.com/${GITHUB_REPOSITORY}/compare/BASE_TAG...TARGET`.
- Include a clear `Ship` or `Block` call.
- Include risk levels: `High`, `Medium`, `Low`, or `None`.
- If no risks are found, include `No material risks identified`.
- Output only the report, with no code fences and no extra commentary.
