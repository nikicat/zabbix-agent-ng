#!/usr/bin/python

fields = {
    'rx':
    {
        'bytes': 1,
        'packets': 2,
    },
    'tx':
    {
        'bytes': 9,
        'packets': 10,
    },
}

def get_stat(lines, iface, dir, units):
    for line in lines:
        if line.strip().startswith(iface):
            return line.split()[fields[dir][units]]

def main(iface, dir, units):
    return get_stat(open('/proc/net/dev').readlines(), iface, dir, units)

def vmain(combinations):
    results = []
    lines = open('/proc/net/dev').readlines()
    for args in combinations:
        results.append(get_stat(lines, *args))
    return results

if __name__ == '__main__':
    import sys
    if len(sys.argv) != 4:
        print('Usage: {0} <iface> <rx|tx> <bytes|packets>'.format(sys.argv[0]))
        sys.exit(1)
    print(main(sys.argv[1], sys.argv[2], sys.argv[3]))
