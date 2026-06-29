# Releases and installation

## Release model

`relayna` v1 is published through GitHub Releases.

- Releases page: [github.com/sarattha/relayna/releases](https://github.com/sarattha/relayna/releases)
- Source repository: [github.com/sarattha/relayna](https://github.com/sarattha/relayna)

Each release publishes:

- a wheel for direct installation
- a source distribution for source-based installs

## Install the wheel

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.4.26/relayna-1.4.26-py3-none-any.whl
```

## Install the source distribution

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.4.26/relayna-1.4.26.tar.gz
```

## Build artifacts locally

```bash
uv build
```

Expected artifacts:

- `dist/relayna-1.4.26.tar.gz`
- `dist/relayna-1.4.26-py3-none-any.whl`

## Versioning policy

The SDK, Studio backend, and Studio frontend share one stable SemVer release
line. The documented SDK API, documented Studio backend API, and
frontend/backend Studio contract follow semantic versioning. Undocumented
internals may change outside of SemVer guarantees.
