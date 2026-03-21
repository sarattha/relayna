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
pip install https://github.com/sarattha/relayna/releases/download/v1.1.6/relayna-1.1.6-py3-none-any.whl
```

## Install the source distribution

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.1.6/relayna-1.1.6.tar.gz
```

## Build artifacts locally

```bash
uv build
```

Expected artifacts:

- `dist/relayna-1.1.6.tar.gz`
- `dist/relayna-1.1.6-py3-none-any.whl`

## Versioning policy

`relayna` follows semantic versioning for the documented public API only. The
stable v1 surface is the documented symbol set from the supported submodules.
Undocumented internals may change outside of semver guarantees.
