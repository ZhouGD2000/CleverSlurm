import argparse
import json

from cslurm import doctor


def main():
    parser = argparse.ArgumentParser(prog='cdoctor')
    sub = parser.add_subparsers(dest='action')
    sub.add_parser('health')
    sub.add_parser('backup').add_argument('destination', nargs='?')
    sub.add_parser('verify').add_argument('source')
    restore = sub.add_parser('restore')
    restore.add_argument('source')
    restore.add_argument('new_directory')
    sub.add_parser('freeze-history', help='Suppress existing unsent messages and exclude recorded non-active jobs from future notification creation.')
    args = parser.parse_args()
    if args.action == 'backup':
        result = doctor.backup(args.destination)
    elif args.action == 'verify':
        result = doctor.verify(args.source)
    elif args.action == 'restore':
        result = doctor.restore(args.source, args.new_directory)
    elif args.action == 'freeze-history':
        result = doctor.freeze_history()
    else:
        result = doctor.health()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
