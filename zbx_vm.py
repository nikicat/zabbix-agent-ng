import sys
import psutil

def main(expr):
    vars = {'free': psutil.avail_virtmem(),
            'available': psutil.avail_virtmem(),
            'pfree': psutil.avail_phymem(),
            'buffers': psutil.phymem_buffers(),
            'total': psutil.total_virtmem(),
            'cached': psutil.cached_phymem(),
    }
    return eval(expr, {}, vars)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: {0} mode'.format(sys.argv[0]))
        sys.exit(1)
    print(main(sys.argv[1]))
