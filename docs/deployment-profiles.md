# Deployment profiles

Adopt the components your application needs. These profiles describe integration choices that can be validated independently.

| Profile | Components | What it enables |
| --- | --- | --- |
| 1 · Developer | SDK and Spooltail | Local asynchronous work, durable results, and event replay |
| 2 · Observable agents | Profile 1 and Studio | Service discovery, task investigation, and supported recovery operations |
| 3 · Governed platform | Profile 2 and Gateway | Authenticated task access, service grants, and metered inference |
| 4 · Secure autonomous agents | Profile 3 and Cell | Restricted execution with sandbox audit and scoped inference |

## Profile 1 · Run a worker

Start with the [SDK Spooltail guide](https://github.com/Relayna/relayna-sdk/blob/main/docs/spooltail.md). The SDK also supports independent deployments with [RabbitMQ and Redis](https://github.com/Relayna/relayna-sdk/blob/main/docs/getting-started.md).

## Profile 2 · Connect Studio

Deploy [Studio](https://github.com/Relayna/relayna-studio/blob/main/docs/studio-backend.md) with its PostgreSQL and Redis dependencies. Register a reachable SDK service endpoint and let Studio discover its capabilities. Use the capabilities to confirm which history, execution, and recovery views are available.

## Profile 3 · Put Gateway in front

Follow the [local quickstart](quickstart.md) to exercise the full task route. Gateway scopes client keys to registered services. Add an inference provider and provider policy when handlers need model access. See [Gateway's configuration guides](https://github.com/Relayna/relayna-gateway) for identity, model access, and usage.

## Profile 4 · Isolate execution

Install [Cell](https://github.com/Relayna/relayna-cell) on a compatible Linux host or VM with its supported runtime. Use [Protocol's Cell instructions](https://github.com/Relayna/relayna-protocol/blob/main/docs/quickstart.md#opt-in-to-standalone-cell) to connect the SDK launcher to Cell's operator socket. The supervisor owns the broker credentials. The workload receives only its task input and the execution capabilities granted by its profile.

Use the [Protocol Helm guide](https://github.com/Relayna/relayna-protocol/blob/main/docs/quickstart.md#install-the-helm-chart-locally) to evaluate the reference deployment on a local cluster. Capacity planning, isolation review, and operational hardening are part of the [production roadmap](roadmap.md).
