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

def main(iface, dir, units):
    for line in open('/proc/net/dev').readlines():
        if line.strip().startswith(iface):
            return line.split()[fields[dir][units]]

if __name__ == '__main__':
    import sys
    if len(sys.argv) != 4:
        print('Usage: {0} <iface> <rx|tx> <bytes|packets>'.format(sys.argv[0]))
        sys.exit(1)
    print(main(sys.argv[1], sys.argv[2], sys.argv[3]))
