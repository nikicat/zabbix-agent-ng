def main():
    return open('/proc/loadavg').readline().split(' ', 1)[0]
