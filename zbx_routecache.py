#!/usr/bin/env python2

def main():
    return int(open("/proc/net/stat/rt_cache").readlines()[1].split(" ")[0], 16)

if __name__ == '__main__':
    print(main())
