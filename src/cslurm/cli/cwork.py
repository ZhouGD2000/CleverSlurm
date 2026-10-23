import argparse
import json

from cslurm.db import connect, init_db
from cslurm.work import drain, retry, wake


def main():
    parser = argparse.ArgumentParser(prog='cwork')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('list')
    sub.add_parser('run')
    sub.add_parser('retry').add_argument('id', type=int)
    args = parser.parse_args()
    if args.action == 'retry':
        retry(args.id)
        wake()
        print('Queued')
    elif args.action == 'run':
        print(drain())
    else:
        with connect() as conn:
            init_db(conn)
            print(json.dumps([dict(r) for r in conn.execute('select * from work_items order by id desc limit 200')], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
