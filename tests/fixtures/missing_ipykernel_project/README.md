Fixture project for smoke-testing missing-`ipykernel` recovery.

The `.venv/bin/python` wrapper only supports the module-probe calls that
`KernelProvisioner` uses during `doctor` and `start`. It deliberately reports:

- `sys` as available
- `ipykernel_launcher` as missing
- `pip` as missing

This keeps the smoke path deterministic without mutating a real virtualenv.
