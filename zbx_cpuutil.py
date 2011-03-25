def get_stat(lines, cpu, counter_name):
    if cpu == 'all':
        cpu = 'cpu '
    else:
        cpu = 'cpu{0} '.format(cpu)
    for line in lines:
        if line.startswith(cpu):
            user, nice, system, idle, wait, irq, softirq, steal, guest = line[4:].split(' ')[1:10]
            if type(counter_name) is list:
                return [locals()[name] for name in counter_name]
            else:
                return locals()[counter_name]

def main(cpu, counter_name):
    return get_stat(open('/proc/stat').readlines(), cpu, counter_name)

def vmain(combinations):
    lines = open('/proc/stat').readlines()
    results = []
    for args in combinations:
        results.append(get_stat(lines, *args))

    return results

if __name__ == '__main__':
    import sys
    print(main(sys.argv[1], sys.argv[2].split(',')))
