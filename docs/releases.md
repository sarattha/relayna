# Releases and monorepo migration

Use [Relayna SDK](https://github.com/Relayna/relayna-sdk) for Python runtime and transport development. The installed Python distribution is still named `relayna`. Use [Relayna Studio](https://github.com/Relayna/relayna-studio) for the backend, web application, and deployment docs. The backend distribution is named `relayna-studio`.

- [SDK installation and release channels](https://github.com/Relayna/relayna-sdk/blob/main/docs/releases.md) use `sdk-vX.Y.Z` tags.
- [Studio installation and release channels](https://github.com/Relayna/relayna-studio/blob/main/docs/releases.md) use `studio-vX.Y.Z` tags. Use the paired backend and frontend artifacts.
- [Protocol's compatibility lock](https://github.com/Relayna/relayna-protocol/blob/main/deploy/sources.json) pins a tested set of component sources for the reference stack.

If an application installed the SDK from GitHub `relayna` **main**, point it to a reviewed SDK revision in `https://github.com/Relayna/relayna-sdk.git`. Studio source builds resolve the SDK version from the Studio backend lockfile.

The [combined release archive](https://github.com/Relayna/relayna/releases) contains the historical wheels and release notes, including [v1.14.0](https://github.com/Relayna/relayna/releases/tag/v1.14.0). The [last monorepo revision](https://github.com/Relayna/relayna/tree/c3023be4a97b96d3e2449a7485179dbea8e33035) is available for source inspection and earlier documentation.
