# Repository Instructions

This repository is preparing a public, sanitized reverse-engineered LC-231 client. Keep the initial history clean and write for readers who have no access to private research material.

## Public Material

- `README.md`: project purpose, current capabilities, and documentation index.
- `docs/protocol.md`: self-contained transport, byte-level facts, object
  layouts, hypotheses, compatibility limits, and external references.
- `open-lc-231.py`: standalone read-only Python dump client.
- `pyproject.toml`: Python requirement, Bleak dependency, and command-line
  entry point.
- `open-lc-231.yaml`: read-only ESPHome integration.
- `secrets.example.yaml`: placeholder-only local configuration example.

Do not track real device MACs, local IPs, raw Bluetooth captures, generated logs, Home Assistant details, private screenshots, virtual environments, or ESPHome build output.

## Documentation Rules

Write public documentation around what the project currently knows, supports, and does not support. Do not include session history, discarded experiments, or a narrative of how the current state was reached.

Use `docs/protocol.md` for discovery, connection sequencing, frame format, CRC,
request IDs, object layouts, external references, and clearly labeled
confirmed, hypothetical, and unknown conclusions. It must remain sufficient to
implement the client without private research material.

Link to third-party documents instead of copying them into the repository
unless redistribution permission is explicit.

Do not add catch-all public session notes.

## Execution Boundaries

Do not compile, upload, flash, or monitor hardware without explicit user confirmation for that action.

Config validation may be run only when requested or when the current task clearly calls for ESPHome config validation.

For documentation and repository hygiene tasks, use static checks only:

```sh
git diff --check
git status --short --ignored
```

Do not commit unless explicitly instructed.

## Protocol Boundaries

Read-only probing comes first. Pump control is safety-sensitive and must remain out of scope until the protocol, controller behavior, and failure modes are explicitly documented.
