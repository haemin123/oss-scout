"""OSS Scout version information."""

VERSION = "0.4.0"
VERSION_NAME = "Precision Surgeon"


def get_version_info() -> dict[str, str | int]:
    """Return full version information."""
    return {
        "version": VERSION,
        "name": VERSION_NAME,
        "tools": 22,
        "agents": 8,
        "commands": 21,
        "recipes": 8,
        "wiring_templates": 8,
        "tests": 460,
    }


def get_status_line() -> str:
    """Return a compact status line for terminal display."""
    info = get_version_info()
    return (
        f"OSS Scout v{info['version']} ({info['name']}) "
        f"| {info['tools']} tools | {info['commands']} commands"
    )
