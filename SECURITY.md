# Security policy

## Supported version

Security fixes are applied to the latest release line.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Security → Report a vulnerability** flow when it is enabled for the repository. Avoid opening a public issue for path traversal, arbitrary file writes, dependency compromise, or accidental demo-data disclosure.

## Data handling

The parser processes demos locally and does not upload match data. Generated Parquet and dashboard files can contain player identifiers, names, positions, and match details; treat them as potentially sensitive and review them before sharing.
