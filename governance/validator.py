"""Org spec validation — checks schema, references, and prints enforcement summary."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_NORM_TYPES = frozenset([
    "scope", "protected", "readonly", "required_before",
    "forbidden_command", "obliged", "forbidden",
])

VALID_SEVERITIES = frozenset(["hard", "soft"])

VALID_CAPABILITIES = frozenset([
    "read_file", "write_file", "execute_command",
])


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0


def validate_spec(spec_dict: dict) -> ValidationResult:
    """Validate an org spec dictionary."""
    result = ValidationResult()

    if "organization" not in spec_dict:
        result.errors.append("Missing required field: 'organization'")
    if "roles" not in spec_dict:
        result.errors.append("Missing required field: 'roles'")
        return result

    roles = spec_dict.get("roles", {})
    if not isinstance(roles, dict):
        result.errors.append("'roles' must be a mapping")
        return result

    defined_roles = set(roles.keys())
    defined_objectives: set[str] = set()

    for role_name, role_def in roles.items():
        if not isinstance(role_def, dict):
            result.errors.append(f"Role '{role_name}' must be a mapping")
            continue
        objectives = role_def.get("objectives", [])
        defined_objectives.update(objectives)
        caps = role_def.get("capabilities", [])
        for cap in caps:
            if cap not in VALID_CAPABILITIES:
                result.warnings.append(f"Role '{role_name}': unrecognized capability '{cap}'")
        result.summary.append(
            f"Role '{role_name}': {len(objectives)} objective(s), {len(caps)} capability(ies)"
        )

    # Validate norms.
    for i, norm in enumerate(spec_dict.get("norms", [])):
        label = f"Norm #{i + 1}"
        role = norm.get("role")
        if not role:
            result.errors.append(f"{label}: missing 'role'")
        elif role not in defined_roles:
            result.errors.append(f"{label}: role '{role}' not defined")

        norm_type = norm.get("type")
        if not norm_type:
            result.errors.append(f"{label}: missing 'type'")
        elif norm_type not in VALID_NORM_TYPES:
            result.errors.append(f"{label}: unrecognized type '{norm_type}'")

        # Type-specific required fields.
        if norm_type == "scope" and "paths" not in norm:
            result.errors.append(f"{label}: scope requires 'paths'")
        if norm_type == "protected" and "paths" not in norm:
            result.errors.append(f"{label}: protected requires 'paths'")
        if norm_type == "readonly" and "paths" not in norm:
            result.errors.append(f"{label}: readonly requires 'paths'")
        if norm_type == "required_before" and "requires" not in norm:
            result.errors.append(f"{label}: required_before requires 'requires'")
        if norm_type == "forbidden_command" and "command_pattern" not in norm:
            result.errors.append(f"{label}: forbidden_command requires 'command_pattern'")

        severity = norm.get("severity")
        if severity and severity not in VALID_SEVERITIES:
            result.errors.append(f"{label}: unrecognized severity '{severity}'")

        result.summary.append(f"{label}: {norm_type or '?'} on role '{role or '?'}'")

    # Validate access map.
    access = spec_dict.get("access", {})
    if access:
        valid_levels = {"read-write", "read-only", "no-access"}
        for path, level in access.items():
            if level not in valid_levels:
                result.errors.append(f"access['{path}']: invalid level '{level}' (must be read-write, read-only, or no-access)")
            else:
                result.summary.append(f"access: '{path}' -> {level}")

    # Validate dependencies.
    for i, dep in enumerate(spec_dict.get("dependencies", [])):
        label = f"Dependency #{i + 1}"
        dep_role = dep.get("role")
        depends_on = dep.get("depends_on")
        if dep_role and dep_role not in defined_roles:
            result.errors.append(f"{label}: role '{dep_role}' not defined")
        if depends_on and depends_on not in defined_roles:
            result.errors.append(f"{label}: depends_on role '{depends_on}' not defined")

    return result


def validate_spec_file(path: str | Path) -> ValidationResult:
    """Load and validate an org spec YAML file."""
    path = Path(path)
    if not path.exists():
        result = ValidationResult()
        result.errors.append(f"File not found: {path}")
        return result
    with open(path) as f:
        spec_dict = yaml.safe_load(f)
    if not isinstance(spec_dict, dict):
        result = ValidationResult()
        result.errors.append("Org spec must be a YAML mapping")
        return result
    return validate_spec(spec_dict)
