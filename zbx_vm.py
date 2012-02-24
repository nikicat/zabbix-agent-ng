import sys
import psutil

class ZbxVmException(RuntimeError):
    pass

def main(mode):
    if mode == 'free' or mode == 'available':
        return psutil.avail_virtmem()
    elif mode == 'pfree':
        return psutil.avail_phymem()
    elif mode == 'buffers':
        return psutil.phymem_buffers()
    elif mode == 'total':
        return psutil.total_virtmem()
    elif mode == 'cached':
        return psutil.cached_phymem()
    else:
        raise ZbxVmException('invalid mode {0}, possible modes [free,available,pfree,buffers,total,cached]'.format(mode))

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: {0} mode'.format(sys.argv[0]))
        sys.exit(1)
    print(main(sys.argv[1]))
