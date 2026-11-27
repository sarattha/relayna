# Ecosystem roadmap

The roadmap follows the manifesto and distinguishes what the reference stack demonstrates from work planned for broader adoption.

| Phase | Reference capability or next milestone |
| --- | --- |
| 1 · Unified contracts | Protocol v1.0 covers the single-task HTTP and event profile, shared IDs, and optional Cell correlation. |
| 2 · Spooltail and SDK | The native profile demonstrates durable submission, leased execution, retry, and replay. The SDK also offers RabbitMQ and Redis integration. |
| 3 · Gateway task access | The reference stack tests scoped submission, status, history, events, cancellation, and task usage. |
| 4 · Unified Studio | Studio can inspect task history, broker stats, retry evidence, and configured Gateway or Cell observations. |
| 5 · Cell execution | The optional local profile has demonstrated a restricted Cell running a Gateway-submitted task with scoped inference and audit evidence. |
| 6 · Production hardening | Plan and validate replication and failover, internal authentication, multi-tenant policy, disaster recovery, scaling, and load and failure testing. |
| 7 · Ecosystem distribution | The repos ship independent build workflows and a pinned Compose and Helm reference stack. Maintain a tested compatibility set as releases and examples grow. |

Cross-component accounting for inference, tools, sandbox compute, and storage is a design direction. Broader workflow orchestration is also a design direction alongside the SDK's existing workflow facilities.

Review the [architecture](architecture.md) for ownership and failure boundaries and the [quickstart](quickstart.md) for what can be run now.
