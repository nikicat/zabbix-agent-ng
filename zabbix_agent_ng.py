'''
Created on Aug 12, 2010

@author: nbryskin
'''

import socket
import base64
import struct
import os.path
import re
import sys
import time
import logging
import subprocess
from initd import daemon

class trapper(object):
    def __init__(self, host, server, port=10051):
        self.host = host
        self.server = server
        self.port = port

    def _do_request(self, data):
        sock = socket.socket()
        sock.connect((self.server, self.port))
        sock.send(data)
        return sock.makefile()

    def update_item(self, key, data):
        logging.debug('updating item for host {0} {1}={2}'.format(self.host, key, data))
        key = base64.b64encode(key)
        data = base64.b64encode(str(data))
        host = base64.b64encode(self.host)
        request = '<req><host>{host}</host><key>{key}</key><data>{data}</data></req>'.format(**locals())
        reply = self._do_request(request).read()
        if reply != 'OK':
            raise RuntimeError(reply)

    def item_not_supported(self, key):
        self.update_item(key, 'ZBX_NOTSUPPORTED')

    def _send_req(self, data):
        data_len = struct.pack('<Q', len(data))
        header = 'ZBXD'
        version = '\1'
        msg = '{header}{version}{data_len}{data}'.format(**locals())
        return self._do_request(msg)

    def get_active_checks(self):
        items = []
        for line in self._send_req('ZBX_GET_ACTIVE_CHECKS\n{0}\n'.format(self.host)).readlines():
            if line[:-1] == 'ZBX_EOF':
                break
            key, refresh_time = line.split(':')[:2]
            logging.debug('received active check {0}'.format(line[:-1]))
            items.append((key, int(refresh_time)))
        return items

class script(object):
    bin_dir = '/etc/zabbix/bin'
    def __init__(self, line):
        self.key, self.command = line.split(',', 1)
        logging.debug('initializing script {0} for key {1}'.format(self.command, self.key))
        if self.key.endswith('[*]'):
            self.key = self.key[:-3]
        self.execute = self.execute_shell
        if self.command.split()[0].endswith('.py'):
            module = __import__(self.command.split()[0][:-3])
            if hasattr(module, 'main'):
                self.module_main = module.main
                self.execute = self.execute_module

    def execute_module(self, *args):
        logging.debug('calling {0}.main({1})'.format(self.module_main.__module__, ','.join(args)))
        result = self.module_main(*args)
        if type(result) is float:
            result = '{0:f}'.format(result)
        return result

    def execute_shell(self, *args):
        cmd = self.command
        for i in range(10):
            cmd = cmd.replace('${0}'.format(i+1), i < len(args) and args[i] or '')
        logging.debug('invoking shell: {0}'.format(cmd))
        proc = subprocess.Popen(cmd, cwd=self.bin_dir, stdout=subprocess.PIPE, shell=True)
        output = proc.communicate()[0].split('\n', 1)[0]
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)
        return output

sys.path.append(script.bin_dir)
os.environ['PATH'] = os.pathsep.join([os.environ['PATH'], script.bin_dir])

class item(object):
    def __init__(self, host, key, interval, script, args):
        self.key = key
        self.interval = interval
        self.script = script
        self.args = [arg == '$hostname' and host or arg for arg in args]
        self.last_check = 0

    def check(self):
        self.last_check = time.time()
        logging.debug('executing script {0}[{1}]'.format(self.script.key, ','.join(self.args)))
        return self.script.execute(*self.args)

    def get_timeout(self):
        return self.interval - (time.time() - self.last_check)

class host(object):
    def __init__(self, name, server, update_interval, scripts):
        self.name = name
        self.update_interval = update_interval
        self.scripts = scripts
        self.trapper = trapper(name, server)
        self.last_update = 0
        self.items = []

    item_re = re.compile('^((.+?)(\[(.+)\])?)$')
    def update_active_checks(self):
        try:
            logging.debug('updating item list for host {0}'.format(self.name))
            self.last_update = time.time()
            items = []
            for raw_key, interval in self.trapper.get_active_checks():
                key, bare_key, args = self.item_re.match(raw_key).group(1, 2, 4)
                for script in self.scripts:
                    if script.key == bare_key:
                        items.append(item(self.name, key, interval, script, args and args.split(',') or []))
                        break
            self.items = items
        except BaseException, e:
            logging.error('failed to update active checks list for host {0}: {1}'.format(self.name, e))

    def update(self, item):
        if item == self:
            self.update_active_checks()
        else:
            try:
                self.trapper.update_item(item.key, item.check())
            except BaseException, e:
                logging.warning('failed to update item {0}[{2}] for host {1}: {3}'.format(item.script.key, self.name, ','.join(item.args), e))

    def get_nearest_check(self):
        return min([self] + self.items, key=lambda x: x.get_timeout())

    def get_timeout(self):
        return self.update_interval - (time.time() - self.last_update)

class agent(object):
    config_dir = '/etc/zabbix/conf.d'

    def __init__(self, config):
        self.scripts = []
        self.sleep_time = 0
        self.load_configs()
        self.add_self_tests()
        self.hosts = [host(host_name, config.server, config.update_interval, self.scripts) for host_name in config.hosts.split(',')]

    def load_configs(self):
        for name in os.listdir(self.config_dir):
            self.load_config(os.path.join(self.config_dir, name))

    def get_sleep_time(self):
        return self.sleep_time

    def add_self_tests(self):
        class sleep_time_item(object):
            key = 'agent.sleep_time'
            @classmethod
            def execute(cls):
                return self.sleep_time
        self.scripts.append(sleep_time_item)

    def load_config(self, full_path):
        try:
            for line in open(full_path).readlines():
                if line[0] == '#' or line == '\n':
                    continue
                name, val = line.split('=', 1)
                if name == 'UserParameter':
                    self.parse_config_line(val[:-1])
        except BaseException, e:
            logging.warning('can\'t load config file {0}: {1}'.format(full_path, e))

    def parse_config_line(self, line):
        try:
            self.scripts.append(script(line))
        except BaseException, e:
            logging.warning('can\'t parse line {0}: {1}'.format(line, e))

    def run(self):
        current_timeout = 999999999999
        for host in self.hosts:
            item = host.get_nearest_check()
            timeout = item.get_timeout()
            if timeout < current_timeout:
                current_host = host
                current_timeout = timeout
                current_item = item

        if current_timeout > 0:
            logging.debug('sleeping for {0} seconds'.format(current_timeout))
            time.sleep(current_timeout)
            self.sleep_time += current_timeout
        current_host.update(current_item)

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument('-s', '--server', dest='server', default='monitor-iva1.yandex.net', help='zabbix trapper to connect to')
        parser.add_argument('--hosts', dest='hosts', default='', help='host list, separated by commas')
        parser.add_argument('-p', '--port', dest='port', type=int, default=10051, help='zabbix trapper port')
        parser.add_argument('--update-interval', dest='update_interval', type=int, default=120, help='items update interval')

if __name__ == '__main__':
    daemon(agent, 'zabbix-agent-ng', foreground=True).start()