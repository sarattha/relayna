# Run Relayna locally

Use [Relayna Deploy](https://github.com/Relayna/relayna-deploy) to submit a
task through Gateway, replay its events, and open its result in Studio.

Install Git, Python 3.12 or later, and a running Docker engine with Buildx and
Compose v2. Keep loopback ports `18380`, `18381`, and `18382` available or
use [Deploy's port overrides](https://github.com/Relayna/relayna-deploy#run-the-reference-stack).

```sh
git clone https://github.com/Relayna/relayna-deploy.git
cd relayna-deploy
git checkout --detach fe8df352554e2e961ef27463e6ab673c76c08ade
python3 build.py --init-env
python3 build.py
docker compose --profile gateway --profile studio up -d --wait --wait-timeout 180
python3 smoke.py
```

The builder fetches the component commits recorded in [the source lock](https://github.com/Relayna/relayna-deploy/blob/fe8df352554e2e961ef27463e6ab673c76c08ade/sources.json). The smoke registers the SDK reference service, creates a scoped task key, submits an echo task, and checks the result in Studio.

Look for `Reference workflow passed for reference-…`. Open
[Studio](http://127.0.0.1:18382/), sign in using the
`RELAYNA_STUDIO_OPERATOR_TOKEN` from `.env`, and look up the emitted task ID
in the `reference` service.

Use [Relayna Examples](https://github.com/Relayna/relayna-examples) to submit
another task with the scoped key, run your own native worker, or execute a
workload in Cell. See the [deployment profiles](deployment-profiles.md) to
choose how much of the stack to run.
