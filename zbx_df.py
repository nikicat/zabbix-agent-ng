import os
import itertools

def get_stat(s, mode):
    result = {'total': s.f_frsize * s.f_blocks,
              'avail': s.f_bavail * s.f_bsize,
              'free': s.f_bfree * s.f_bsize,
              'itotal': s.f_files,
              'iavail': s.f_favail,
              'ifree': s.f_ffree}
    return result[mode]

def main(path, mode):
    return get_stat(os.statvfs(path), mode)

def vmain(combinations):
    combinations = map(tuple, combinations)
    results_dict = {}
    for path, grouped_combinations in itertools.groupby(combinations, lambda c: c[0]):
        stat = os.statvfs(path)
        for args in grouped_combinations:
            results_dict[args] = get_stat(stat, args[1])

    results = []
    for args in combinations:
        results.append(results_dict[args])
    return results

if __name__ == '__main__':
    import sys
    res1 = main(sys.argv[1], sys.argv[2])
    res2 = vmain([(sys.argv[1], sys.argv[2])])
    print res1, res2
