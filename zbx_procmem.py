import sys
import psutil
import itertools

class ZbxMemException(Exception):
    pass

def filterproc(name, user, cmdline):
    return itertools.ifilter(
        lambda proc: (name == '' or proc.name == name) and
                     (user == '' or proc.username == user) and
                     (cmdline == '' or (proc.cmdline and proc.cmdline[0] == cmdline)),
        psutil.process_iter())

def main(memtype, name, user, mode, cmdline):
    rsss = list(itertools.imap(lambda proc: getattr(proc.get_memory_info(), memtype), filterproc(name, user, cmdline)))
    if len(rsss) == 0:
        return 0
    if mode == 'sum' or mode == '':
        return sum(rsss)
    elif mode == 'avg':
        return sum(rsss) / len(rsss)
    elif mode == 'min':
        return min(rsss)
    elif mode == 'max':
        return max(rsss)
    else:
        raise ZbxMemException('invalid mode: must be one of [<empty string> or sum,avg,min,max]')

if __name__ == '__main__':
    if len(sys.argv) < 6:
        print('usage: {0} rss|vms <name> <user> <mode>(""|sum|avg|min|max) <cmdline>'.format(sys.argv[0]))
        sys.exit(1)
    print(main(*sys.argv[1:6]))
