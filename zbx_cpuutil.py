def main(cpu, counter_name):
    if cpu == 'all':
        cpu = 'cpu '
    else:
        cpu = 'cpu{0} '.format(cpu)
    for line in open('/proc/stat').readlines():
        if line.startswith(cpu):
            user, nice, system, idle, wait, irq, softirq, steal, guest = line[4:].split(' ')[1:10]
            if type(counter_name) is list:
                return [locals()[name] for name in counter_name]
            else:
                return locals()[counter_name]

if __name__ == '__main__':
    import sys
    print(main(sys.argv[1], sys.argv[2].split(',')))
