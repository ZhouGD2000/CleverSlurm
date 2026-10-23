"""Conservative audit metadata, never execution validation or selector expansion."""

import re


def audit(argv, environ):
    targets = []
    ambiguous = any(k.startswith('SCANCEL_') for k in environ) or bool(environ.get('SLURM_CLUSTERS'))
    signaling = False
    informational = False
    values = {'account', 'clusters', 'jobname', 'name', 'nodelist', 'partition',
              'qos', 'reservation', 'sibling', 'signal', 'state', 'user', 'wckey', 'admin-comment'}
    short_values = dict(zip('AMnwpqRstu', ('account', 'clusters', 'name', 'nodelist',
                                         'partition', 'qos', 'reservation', 'signal', 'state', 'user')))
    index, positional = 0, False
    while index < len(argv):
        arg = argv[index]
        index += 1
        if arg == '--' and not positional:
            positional = True
            continue
        if not positional and arg.startswith('--'):
            name = arg[2:].split('=', 1)[0]
            if name in {'help', 'usage', 'version'}:
                informational = True
            elif name in values:
                if '=' not in arg:
                    if index == len(argv):
                        ambiguous = True
                    index += 1
                signaling |= name == 'signal'
                ambiguous |= name != 'signal'
            elif name in {'batch', 'full'}:
                signaling = True
            elif name not in {'quiet', 'verbose', 'ctld', 'hurry'}:
                ambiguous = True
        elif not positional and arg.startswith('-') and arg != '-':
            for offset, char in enumerate(arg[1:], 1):
                if char in short_values:
                    name = short_values[char]
                    signaling |= name == 'signal'
                    ambiguous |= name != 'signal'
                    if offset == len(arg) - 1:
                        if index == len(argv):
                            ambiguous = True
                        index += 1
                    break
                if char in 'hV':
                    informational = True
                elif char in 'bf':
                    signaling = True
                elif char not in 'QvH':
                    ambiguous = True
        elif re.fullmatch(r'[0-9]+(?:_[0-9]+)?(?:\.[A-Za-z0-9]+)?', arg):
            targets.append(arg)
        else:
            ambiguous = True
    if informational:
        return {'informational': True, 'targets': [], 'events': []}
    if ambiguous or not targets:
        return {'targets': targets, 'events': [[None, 'SCANCEL_REQUESTED']]}
    return {'targets': targets,
            'events': [[target, 'SIGNAL_REQUESTED' if signaling or '.' in target else 'CANCEL_REQUESTED']
                       for target in dict.fromkeys(targets)]}
