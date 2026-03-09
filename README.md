# termfix

Windows terminal assistant with spell correction, frecency directory navigation, and fuzzy command suggestions.

## Install

```
pip install -e ".[dev]"
```

## Usage

```
termfix daemon start     # Start background daemon
termfix init powershell  # Install PowerShell hook
termfix check cladue     # Manual spell check
termfix scan             # Rescan PATH
j podcast                # Jump to frecent directory
```
