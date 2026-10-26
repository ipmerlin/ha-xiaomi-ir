"""Validate and resolve the persistent, device-scoped command library."""

from copy import deepcopy


def command_names(value) -> list[str]:
    """Accept HA's list or one command, but reject missing/empty names."""
    names = [value] if isinstance(value, str) else list(value or [])
    if not names or any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError("Specify at least one non-empty command name")
    return names


def device_name(value) -> str:
    """Named command operations always require a device namespace."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Specify device, for example: tv")
    return value


def validate_library(data) -> dict:
    """Never silently replace corrupt storage with an empty library."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Invalid command storage")
    for device, commands in data.items():
        device_name(device)
        if not isinstance(commands, dict):
            raise ValueError("Invalid device command storage")
        for name, code in commands.items():
            command_names([name])
            if (
                not isinstance(code, str)
                or not code.startswith("raw:")
                or len(code) <= 4
            ):
                raise ValueError("Invalid stored IR code")
    return deepcopy(data)


def resolve_commands(library, commands, device=None) -> list[str]:
    """Resolve every command before transmitting any part of a sequence."""
    result = []
    for name in command_names(commands):
        if name.startswith(("raw:", "pronto:")):
            result.append(name)
        else:
            device_name(device)
            try:
                result.append(library[device][name])
            except KeyError as err:
                raise ValueError(
                    f"Command {name!r} is not learned for {device!r}"
                ) from err
    return result


def delete_commands(library, commands, device) -> dict:
    """Delete atomically: missing names leave the library untouched."""
    device_name(device)
    names = command_names(commands)
    if any(name not in library.get(device, {}) for name in names):
        raise ValueError("One or more commands were not found")
    updated = deepcopy(library)
    for name in set(names):
        del updated[device][name]
    if not updated[device]:
        del updated[device]
    return updated
