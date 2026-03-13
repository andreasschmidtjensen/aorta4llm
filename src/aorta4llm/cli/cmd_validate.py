"""aorta validate — org spec validation command."""


def add_parser(subparsers):
    p = subparsers.add_parser("validate", help="Validate an org spec YAML file")
    p.add_argument("spec", help="Path to org spec YAML file")
    p.set_defaults(func=run)


def run(args):
    from aorta4llm.governance.validator import validate_spec_file

    result = validate_spec_file(args.spec)

    if result.errors:
        print("ERRORS:")
        for e in result.errors:
            print(f"  - {e}")
    if result.warnings:
        print("WARNINGS:")
        for w in result.warnings:
            print(f"  - {w}")
    if result.summary:
        print("\nENFORCEMENT SUMMARY:")
        for s in result.summary:
            print(f"  {s}")

    if result.valid:
        print("\nSpec is valid.")
    else:
        print(f"\nSpec has {len(result.errors)} error(s).")
        raise SystemExit(1)
