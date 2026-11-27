# Run Relayna locally

Use the pinned [Relayna Protocol reference stack](https://github.com/Relayna/relayna-protocol/tree/deecf3dc080c996914a4ef78d4098a7035ed851a/deploy) to submit an authenticated task and see the result in Studio.

## Prerequisites

Install Git, Python 3.12 or later, and a running Docker engine with Buildx and Compose v2. Configure Git access to the pinned component sources. The reference builder uses HTTPS for SDK and Studio and GitHub SSH for Gateway and Spooltail. Keep local ports `18380`, `18381`, and `18382` available, or use the overrides below.

## Start and verify the reference stack

```sh
git clone https://github.com/Relayna/relayna-protocol.git
cd relayna-protocol
git checkout --detach deecf3dc080c996914a4ef78d4098a7035ed851a
python3 deploy/build.py --init-env
python3 deploy/build.py
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile studio up -d --wait --wait-timeout 180
python3 deploy/smoke.py
```

The builder fetches the exact component commits recorded in Protocol. The stack starts Gateway, Spooltail, the SDK reference service, Studio, PostgreSQL, and Redis. The smoke registers the reference service, creates a scoped client key, submits an echo task, and checks the result in Studio, including event replay and cancellation responses.

Look for `Reference workflow passed for reference-…` and copy the emitted task ID. Open [Studio](http://127.0.0.1:18382/), sign in using the `RELAYNA_STUDIO_OPERATOR_TOKEN` from your local `deploy/.env`, and open the task under the `reference` service. The Gateway listens at [port 18380](http://127.0.0.1:18380/) and its operator API at [port 18381](http://127.0.0.1:18381/). The smoke stores the reference task key in `deploy/.local/reference.key`.

## Use alternate ports

Set the Compose ports and pass the same addresses to the smoke if those ports are already in use.

```sh
RELAYNA_GATEWAY_PORT=19560 RELAYNA_GATEWAY_CONTROL_PORT=19561 RELAYNA_STUDIO_PORT=19562 \
  docker compose --env-file deploy/.env -f deploy/compose.yaml --profile studio up -d --wait --wait-timeout 180
python3 deploy/smoke.py --gateway http://127.0.0.1:19560 --control http://127.0.0.1:19561 --studio http://127.0.0.1:19562
```

Open Studio on port `19562` for this run.

## Stop or extend the stack

Run `docker compose --env-file deploy/.env -f deploy/compose.yaml --profile studio down` to stop the reference stack and retain the named data volumes.

See [Protocol's deployment guide](https://github.com/Relayna/relayna-protocol/blob/deecf3dc080c996914a4ef78d4098a7035ed851a/docs/quickstart.md) for manual client requests, Helm, and optional Cell setup. The local quickstart uses a native echo worker. Use the [deployment profiles](deployment-profiles.md) to choose a sandboxed or smaller configuration.
