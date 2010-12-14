'''
Created on Aug 12, 2010

@author: nbryskin
'''

from gevent import monkey
monkey.patch_all()
import gevent
from gevent.event import Event
import socket
import base64
import struct
import os.path
import re
import sys
import signal
import time
import logging
import subprocess
import config
import daemon
import daemon.pidlockfile
import ldap
import json
import itertools
from setproctitle import setproctitle

class trapper(object):
    def __init__(self, host, server, port):
        self.host = host
        self.server = server
        self.port = port
        self.logger = logging.getLogger(host)
        self.decoder = json.JSONDecoder()
        self.encoder = json.JSONEncoder()

    def update_items(self, results):
        clock = int(time.time())
        inner_data = []
        for key, value in results:
            self.logger.debug('updating item {0}={1}'.format(key, value))
            inner_data.append({'host': self.host, 'key': key, 'value': value, 'clock': clock})
        data = {'request': 'agent data', 'clock': clock, 'data': inner_data}
        response = self._send_req(data)
        if response[u'response'] != u'success':
            raise RuntimeError(response)

    def get_active_checks(self):
        items = []
        response = self._send_req({'request': 'active checks', 'host': self.host})
        if response[u'response'] != u'success':
            raise RuntimeError(response)
        return map(lambda i: (i[u'key'], float(i[u'delay'])), response.get(u'data', []))

    def item_not_supported(self, key):
        self.update_item((key, 'ZBX_NOTSUPPORTED'))

    def _send_req(self, data):
        header = 'ZBXD\x01'
        request = self.encoder.encode(data)
        data_len = struct.pack('<Q', len(request))
        self.logger.debug('sending request: {0}'.format(request))
        msg = '{header}{data_len}{data}'.format(header=header, data_len=data_len, data=request)
        sock = self._do_request(msg)
        response = sock.read()
        response = self.decoder.decode(response)
        self.logger.debug('received response: {0}'.format(response))
        return response

    def _do_request(self, data):
        sock = socket.socket()
        sock.connect((self.server, self.port))
        sock.send(data)
        return sock.makefile()

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
            if hasattr(module, 'main') or hasattr(module, 'vmain'):
                self.module = module
                self.execute = self.execute_module

    def execute_module(self, args_combinations):
        if hasattr(self.module, 'vmain'):
            logging.debug('calling {0}.vmain({1})'.format(self.module, args_combinations))
            result = self.module.vmain(args_combinations)
        else:
            logging.debug('calling {0}.main({1})'.format(self.module, args_combinations))
            result = [self.module.main(*args) for args in args_combinations]
        return result

    def execute_shell(self, args_combinations):
        result = []
        for args in args_combinations:
            cmd = self.command
            for i in range(10):
                cmd = cmd.replace('${0}'.format(i+1), i < len(args) and args[i] or '')
            logging.debug('invoking shell: {0}'.format(cmd))
            proc = subprocess.Popen(cmd, cwd=self.bin_dir, stdout=subprocess.PIPE, shell=True)
            result.append(proc.communicate()[0].split('\n', 1)[0])
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd)
        return result

sys.path.append(script.bin_dir)
os.environ['PATH'] = os.pathsep.join([os.environ['PATH'], script.bin_dir])

class item(object):
    def __init__(self, host, key, interval, script, args):
        self.host = host
        self.key = key
        self.interval = interval
        self.script = script
        self.logger = logging.getLogger(host)
        self.args = [arg == '$hostname' and host or arg for arg in args]

    def __eq__(self, other):
        return self.key == other.key and self.interval == other.interval

    def __hash__(self):
        return (self.key, self.interval).__hash__()

    def __str__(self):
        return self.key

class coupled_item(object):
    def __init__(self, script, trapper):
        self.items = set()
        self.script = script
        self.logger = logging.getLogger('coupled_item.{0}'.format(script))
        self.trapper = trapper

    def update(self, items):
        assert len(items) > 0
        for i in items:
            assert i.script == self.script, 'trying to insert item with different script to coupled_item: {0} != {1}'.format(i.script, self.script)
        self.items |= set(items)
        self.interval = min(self.items, key=lambda i: i.interval).interval

    def check_loop(self):
        assert len(self.items) > 0
        assert type(self.interval) is float, 'type of interval is {0}'.format(type(self.interval))
        while True:
            try:
                self.check()
            except Exception, e:
                self.logger.exception(e)
            time.sleep(self.interval)

    def check(self):
        items = self.items
        args_combinations = [i.args for i in items]
        results = self.script.execute(args_combinations)
        self.trapper.update_items([(i.key, r) for i, r in zip(items, results)])

class host(object):
    def __init__(self, name, options, scripts):
        self.name = name
        self.update_interval = options.update_interval
        self.scripts = scripts
        self.logger = logging.getLogger(name)
        self.trapper = trapper(name, options.server, options.port)
        self.items = set()
        self.coupled_items = []

    def loop(self):
        while True:
            self.update_active_checks()
            time.sleep(self.update_interval)
        gevent.joinall([i.job for i in self.items])

    item_re = re.compile('^((.+?)(\[(.+)\])?)$')
    def update_active_checks(self):
        try:
            self.logger.debug('updating item list')
            retrieved_items = set()
            for raw_key, interval in self.trapper.get_active_checks():
                key, bare_key, args = self.item_re.match(raw_key).group(1, 2, 4)
                for script in self.scripts:
                    if script.key == bare_key:
                       retrieved_items.add(item(self.name, key, interval, script, args and args.split(',') or []))
                       break
            added_items = retrieved_items - self.items
            removed_items = self.items - retrieved_items
            self.items -= removed_items
            self.items |= added_items
            if added_items:
                self.logger.info('added items: {0}'.format(', '.join(map(str, added_items))))
            if removed_items:
                self.logger.info('removed items: {0}'.format(', '.join(map(str, removed_items))))
            for ci in self.coupled_items:
                ci.items -= removed_items
            jobs_to_kill = []
            for ci in self.coupled_items[:]:
                if len(ci.items) == 0:
                    jobs_to_kill.append(ci.job)
                    self.coupled_items.remove(ci)
            gevent.killall(jobs_to_kill, block=True)

            added_items = sorted(list(added_items), key=lambda i: i.script)
            grouped_items = itertools.groupby(added_items, lambda i: i.script)
            for script, items in grouped_items:
                cis = [ci for ci in self.coupled_items if ci.script == script]
                if len(cis) > 0:
                    assert len(cis) == 1, 'more than one coupled_item for script {0}'.format(script)
                    ci.update(list(items))
                else:
                    ci = coupled_item(script, self.trapper)
                    ci.update(list(items))
                    ci.job = gevent.spawn(ci.check_loop)
                    self.coupled_items.append(ci)

            if not self.items:
                self.logger.info('no items')
        except Exception, e:
            self.logger.exception(e)#, 'failed to update active checks list')

    def get_nearest_check(self):
        return min([self] + self.items, key=lambda x: x.get_timeout())

    def get_timeout(self):
        return self.update_interval - (time.time() - self.last_update)

class agent(object):
    def __init__(self):
        self.scripts = []
        self.logger = logging.getLogger()
        self.load_config()
        self.load_zabbix_configs()
        self.hosts = [host(id, self.options, self.scripts) for id in self.get_ldap_ids(self.options.ldap)]

    def get_sleep_time(self):
        return self.sleep_time

    def load_config(self):
        parser = config.config_parser('zabbix_agent_ng')
        parser.add_argument('--update-interval', type=int, default=120, help='items update interval')
        parser.add_argument('--server', help='zabbix trapper server')
        parser.add_argument('--port', type=int, default=10051, help='zabbix trapper port')
        parser.add_argument('--ldap', default='ldap://localhost', help='address of LDAP server with tunnels info')
        parser.add_argument('--pid-file', default='/var/run/zabbix-agent-ng.pid', help='path to pid file')
        parser.add_argument('--zabbix-conf-dir', default='/etc/zabbix', help='path to zabbix config')
        parser.add_argument('--daemonize', type=int, default=0, help='daemonize after start')
        parser.parse()
        self.options = parser.options
        parser.init_logging()

    def get_ldap_ids(self, host):
        l = ldap.initialize(host)
        for dn, entry in l.search_s('dc=local,dc=net', ldap.SCOPE_SUBTREE, 'cn=homer*'):
            yield entry['cn'][0].replace('homer', 'tunnel')
        for dn, entry in l.search_s('dc=local,dc=net', ldap.SCOPE_SUBTREE, 'cn=tunnel*'):
            yield entry['cn'][0]
        yield socket.gethostbyaddr(socket.gethostname())[0]

    def load_zabbix_configs(self):
        conf_d = os.path.join(self.options.zabbix_conf_dir, 'conf.d')
        for name in os.listdir(conf_d):
            self.load_zabbix_config(os.path.join(conf_d, name))

    def load_zabbix_config(self, full_path):
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

    def daemonize(self):
        self.context = daemon.DaemonContext()
        # ugly hack to prevent closing of epoll queue
        self.context.files_preserve = range(daemon.daemon.get_maximum_file_descriptors())
        self.context.prevent_core = False
        self.context.pidfile = daemon.pidlockfile.TimeoutPIDLockFile(self.options.pid_file, 1)
        self.context.open()

    def run(self):
        if self.options.daemonize:
            self.daemonize()
        setproctitle('zabbix-agent-ng')
        waiter = Event()
        waiter.clear()
        gevent.signal(signal.SIGTERM, waiter.set)
        jobs = [gevent.spawn(host.loop) for host in self.hosts]
        waiter.wait()
        self.logger.info('exiting')

if __name__ == '__main__':
    a = agent()
    a.run()
