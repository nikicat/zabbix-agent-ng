#!/usr/bin/python

from itertools import izip, chain
from collections import namedtuple
import logging

def get_stat():
    lines_snmp = open('/proc/net/snmp').readlines()
    lines_netstat = open('/proc/net/netstat').readlines()
    args = [chain(lines_snmp, lines_netstat)] * 2
    groups = {}
    for names, values in izip(*args):
        group1, namelist = names.split(':')
        group2, valuelist = values.split(':')
        assert(group1 == group2)
        group_type = namedtuple(group1, namelist.split())
        groups[group1] = group_type(*map(int, valuelist.split()))
    return groups

def main(expr):
    return vmain([expr])[0]

prev_stat = None

def vmain(combinations):
    stat = get_stat()
    stat_tuple_type = namedtuple('Stat', stat.keys())
    stat_tuple = stat_tuple_type(*stat.values())
    global prev_stat
    if prev_stat is None:
        prev_stat = stat_tuple
    results = []
    for expr in combinations:
        try:
            locals_ = stat
            locals_.update({'prev': prev_stat})
            result = eval(expr[0], {}, locals_)
        except Exception, e:
            logging.getLogger('zabbix-agent-ng').warning('failed to evaluate expression {0} with locals={1}: {2}'.format(expr, stat, e))
            result = None
        results.append(result)
    prev_stat = stat_tuple
    return results

if __name__ == '__main__':
    import sys
    print(main(sys.argv[1]))
