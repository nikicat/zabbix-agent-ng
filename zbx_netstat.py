#!/usr/bin/python

from itertools import izip, chain

def get_stat():
    lines_snmp = open('/proc/net/snmp').readlines()
    lines_netstat = open('/proc/net/netstat').readlines()
    args = [chain(lines_snmp, lines_netstat)] * 2
    groups = {}
    for names, values in izip(*args):
        group1, namelist = names.split(':')
        group2, valuelist = values.split(':')
        assert(group1 == group2)
        groups[group1] = dict(zip(namelist.split(), valuelist.split()))
    return groups

def main(type, name):
    return get_stat()[str(type)][str(name)]

def vmain(combinations):
    stat = get_stat()
    results = []
    for type, name in combinations:
        results.append(stat[str(type)][str(name)])
    return results

if __name__ == '__main__':
    import sys
    print(main(sys.argv[1], sys.argv[2]))
