# Relayna

Relayna provides the infrastructure to run, govern, and observe asynchronous agent tasks. It connects durable task delivery, SDK workers, policy-controlled access, optional sandboxed execution, and an operator console.

**[Quickstart](docs/quickstart.md)** · [Architecture](docs/architecture.md) · [Deployment profiles](docs/deployment-profiles.md) · [Roadmap](docs/roadmap.md)

| Component | Responsibility |
| --- | --- |
| [Relayna Protocol](https://github.com/Relayna/relayna-protocol) | Shared contracts and the reference stack |
| [Relayna Gateway](https://github.com/Relayna/relayna-gateway) | Task and inference access, routing, policy, and usage |
| [Relayna Spooltail](https://github.com/Relayna/relayna-spooltail) | Durable work, fenced leases, and replayable task events |
| [Relayna SDK](https://github.com/Relayna/relayna-sdk) | Python integration for task submission, worker execution, and reporting |
| [Relayna Cell](https://github.com/Relayna/relayna-cell) | Isolated agent execution, network policy, and audit evidence |
| [Relayna Studio](https://github.com/Relayna/relayna-studio) | Task investigation, telemetry, and operator recovery |

Start with the local reference stack to submit a task through Gateway, let an SDK worker complete it on Spooltail, and inspect its result and history in Studio. Use the [deployment profiles](docs/deployment-profiles.md) to choose the components your application needs.

Read the [documentation site](https://relayna.github.io/relayna/) or find historical packages in the [release archive](https://github.com/Relayna/relayna/releases). SDK and Studio development now lives in their component repositories. [Migration notes](docs/releases.md) cover source installs and existing release links.
