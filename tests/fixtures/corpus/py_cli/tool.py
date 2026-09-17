"""Batch reconciliation tool.

A command-line paradigm: subcommands rather than network handlers. Two of the three
verbs change state; one only reports.
"""
import argparse

from ledger import apply_adjustment, reconcile, summarise


def cmd_report(args):
    """Print a reconciliation summary without changing anything."""
    return summarise(args.period)


def cmd_reconcile(args):
    """Reconcile the period and write the corrected balances."""
    return reconcile(args.period, dry_run=False)


def cmd_adjust(args):
    """Post a manual adjustment against a single account."""
    if args.amount == 0:
        raise SystemExit("an adjustment of zero changes nothing")
    return apply_adjustment(args.account, args.amount)


def build_parser():
    parser = argparse.ArgumentParser(prog="ledger-tool")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="Show a reconciliation summary")
    report.add_argument("period")
    report.set_defaults(handler=cmd_report)

    run = sub.add_parser("reconcile", help="Reconcile a period and write balances")
    run.add_argument("period")
    run.set_defaults(handler=cmd_reconcile)

    adjust = sub.add_parser("adjust", help="Post a manual adjustment")
    adjust.add_argument("account")
    adjust.add_argument("amount", type=int)
    adjust.set_defaults(handler=cmd_adjust)
    return parser


def main():
    """Command-line entry point."""
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    main()
