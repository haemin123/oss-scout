"""OSS Scout version information."""

VERSION = "0.3.0"
VERSION_NAME = "Assembly Engine"


def get_version_info() -> dict:
    """Return full version information."""
    return {
        "version": VERSION,
        "name": VERSION_NAME,
        "tools": 19,
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
