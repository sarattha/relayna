# Component directory

| Repository | What it owns | Start here |
| --- | --- | --- |
| [Relayna](https://github.com/Relayna/relayna) | Ecosystem documentation and architecture | [Quickstart](quickstart.md) |
| [Protocol](https://github.com/Relayna/relayna-protocol) | Versioned contracts, compatibility checks, and the reference deployment | [v1.0 contracts](https://github.com/Relayna/relayna-protocol/tree/main/contracts/v1.0) |
| [Spooltail](https://github.com/Relayna/relayna-spooltail) | Durable tasks, leases, and replayable event topics | [Broker setup](https://github.com/Relayna/relayna-spooltail#readme) |
| [SDK](https://github.com/Relayna/relayna-sdk) | Python worker runtime and transport adapters | [SDK setup](https://github.com/Relayna/relayna-sdk/blob/main/docs/getting-started.md) |
| [Studio](https://github.com/Relayna/relayna-studio) | Operations, observability, and recovery | [Studio deployment](https://github.com/Relayna/relayna-studio/blob/main/docs/studio-backend.md) |
| [Gateway](https://github.com/Relayna/relayna-gateway) | Authentication, task and inference routing, and usage policy | [Gateway setup](https://github.com/Relayna/relayna-gateway#readme) |
| [Cell](https://github.com/Relayna/relayna-cell) | Sandbox execution, policy, and audit evidence | [Cell setup](https://github.com/Relayna/relayna-cell#readme) |

The [reference Compose and Helm files](https://github.com/Relayna/relayna-protocol/tree/main/deploy) and the [SDK reference service](https://github.com/Relayna/relayna-sdk/blob/main/examples/spooltail_service.py) are available today. The manifesto assigns future dedicated homes to [Relayna Deploy](https://github.com/Relayna/relayna-deploy) and cross-component examples.

Use the [release and migration notes](releases.md) to select SDK, Studio, and historical artifacts.
