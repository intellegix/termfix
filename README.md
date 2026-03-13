# termfix

Windows terminal assistant with spell correction, frecency directory navigation, and fuzzy command suggestions. Like [thefuck](https://github.com/nvbn/thefuck) + [zoxide](https://github.com/ajeetdsouza/zoxide) + shell history search, built for Windows.

## Features

- **Spell correction** — mistype `gti` and get `git` suggested instantly via a background daemon
- **Frecency directories** — jump to frequently/recently used directories with `j <query>`
- **Fuzzy command suggestions** — search and complete commands from your shell history

## Requirements

- Windows 10/11
- Python 3.11+

## Install

```
pip install termfix
```

Or from source:

```
git clone https://github.com/intellegix/termfix.git
cd termfix
pip install -e ".[dev]"
```

## Quickstart

```powershell
# 1. Start the background daemon
termfix daemon start

# 2. Install the PowerShell hook
termfix init powershell

# 3. Restart PowerShell — termfix is active
```

## Usage

### Spell correction

Mistyped commands are automatically caught by the shell hook:

```
> gti status
termfix: did you mean 'git'?
```

Manual check:

```
termfix check cladue
```

### Directory jumping

```powershell
j projects       # Jump to most frecent directory matching "projects"
j                # List top 10 frecent directories
```

### Daemon management

```
termfix daemon start    # Start background daemon
termfix daemon stop     # Stop daemon
termfix daemon status   # Show daemon status
termfix daemon run      # Run in foreground (debugging)
```

### Configuration

```
termfix config show     # Show current settings
termfix config edit     # Open config.toml in editor
```

Settings can be overridden via environment variables (`TERMFIX_SPELL_MAX_DISTANCE`, etc.) or `~/.termfix/config.toml`.

### Import history

Seed the database from PSReadLine history:

```
termfix import-history
```

## Architecture

- **Background daemon** — persistent `pythonw.exe` process, avoids 65-700ms Python startup per command
- **Named Pipes IPC** — length-prefixed JSON over `\\.\pipe\TermfixPipe`, sub-5ms latency
- **SQLite WAL** — concurrent reads with automatic cloud drive detection (falls back to rollback journal on OneDrive/Dropbox/UNC paths)
- **Mozilla frecency** — time-weighted visit scoring with configurable aging threshold

## Development

```
pip install -e ".[dev]"
pytest --tb=short -q
ruff check src/ tests/
hatch build
```

## License

MIT
