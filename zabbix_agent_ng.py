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

class Sender(object):
    def __init__(self, options):
        self.logger = logging.getLogger('Sender')
        self.logger.info('created Sender; options={0}'.format(options))
        self.server = options.server
        self.port = options.port
        if options.protocol == '1.4':
            self.get_active_checks = self._get_active_checks_14
            self.send_items = self._send_items_14
            self.send_req = self._send_req_14
        elif options.protocol == '1.8':
            self.get_active_checks = self._get_active_checks_18
            self.send_items = self._send_items_18
            self.send_req = self._send_req_18
        else:
            raise InvalidArgument('protocol must be one of 1.4 or 1.8')
        self.decoder = json.JSONDecoder()
        self.encoder = json.JSONEncoder()

    def _get_active_checks_14(selfi, host):
        items = []
        for line in self.send_req('ZBX_GET_ACTIVE_CHECKS\n{0}\n'.format(host)).readlines():
            if line[:-1] == 'ZBX_EOF':
                break
            key, delay = line.split(':')[:2]
            self.logger.debug('received active check {0}'.format(line[:-1]))
            items.append((key, float(delay)))
        return items

    def _send_items_14(self, items, values, _timestamp):
        for item, value in zip(items, values):
            self.logger.debug('updating item {0}={1}'.format(item.key, value))
            host = base64.b64encode(item.host)
            key = base64.b64encode(item.key)
            data = base64.b64encode(str(value))
            request = '<req><host>{host}</host><key>{key}</key><data>{data}</data></req>'.format(**locals())
            reply = self._do_request(request).read()
            if reply != 'OK':
                raise RuntimeError(reply)

    def _send_req_14(self, data):
        data_len = struct.pack('<Q', len(data))
        header = 'ZBXD'
        version = '\1'
        msg = '{header}{version}{data_len}{data}'.format(**locals())
        return self._do_request(msg)

    ZABBIX_18_MAX_REQUEST = 20
    def _send_items_18(self, items, values, timestamp):
        item_value = zip(items, values)
        for i, group in itertools.groupby(enumerate(item_value), key=lambda i: i[0]/self.ZABBIX_18_MAX_REQUEST):
            inner_data = []
            for i, (item, value) in group:
                self.logger.debug('sending item [{2}]{0}={1}'.format(item.key, value, item.host))
                inner_data.append({'host': item.host, 'key': item.key, 'value': value, 'clock': timestamp})
            data = {'request': 'agent data', 'clock': timestamp, 'data': inner_data}
            response = self.send_req(data)
            if response[u'response'] != u'success':
                raise RuntimeError(response)
            self.logger.debug('items successfully sent: {0}'.format(', '.join(['{0}.{1}'.format(item.host, item.key) for item in items])))

    def _get_active_checks_18(self, host):
        response = self.send_req({'request': 'active checks', 'host': host})
        if response[u'response'] != u'success':
            raise RuntimeError(response)
        return map(lambda i: (i[u'key'], float(i[u'delay'])), response.get(u'data', []))

    def _send_req_18(self, data):
        header = 'ZBXD\x01'
        request = self.encoder.encode(data)
        data_len = struct.pack('<Q', len(request))
        self.logger.debug('sending request: {0}'.format(request))
        msg = '{header}{data_len}{data}'.format(header=header, data_len=data_len, data=request)
        response_data = self._do_request(msg).read()
        response = self.decoder.decode(response_data)
        self.logger.debug('received response: {0}'.format(response))
        return response

    def item_not_supported(self, key):
        self.update_item((key, 'ZBX_NOTSUPPORTED'))

    def _do_request(self, data):
        sock = socket.socket()
        sock.connect((self.server, self.port))
        sock.send(data)
        return sock.makefile()

class Script(object):
    bin_dir = '/etc/zabbix/bin'
    def __init__(self, line, sender):
        self.key, self.command = line.split(',', 1)
        self.logger = logging.getLogger(str(self))
        self.logger.debug('initializing with command {0}'.format(self.command))
        if self.key.endswith('[*]'):
            self.key = self.key[:-3]
        self.execute = self.execute_shell
        if self.command.split()[0].endswith('.py'):
            module = __import__(self.command.split()[0][:-3])
            if hasattr(module, 'main') or hasattr(module, 'vmain'):
                self.module = module
                self.execute = self.execute_module
        self.items = set()
        self.sender = sender
        self.check_job = gevent.Greenlet(self.check_loop)

    def __str__(self):
        return '<script {0}>'.format(self.key)

    def execute_module(self, args_combinations):
        if hasattr(self.module, 'vmain'):
            self.logger.debug('calling {0}.vmain({1})'.format(self.module.__name__, args_combinations))
            results = self.module.vmain(args_combinations)
            self.logger.debug('called {0}.vmain({1})'.format(self.module.__name__, args_combinations))
        else:
            self.logger.debug('calling {0}.main({1})'.format(self.module.__name__, args_combinations))
            results = []
            for args in args_combinations:
                try:
                    result = self.module.main(*args)
                except Exception, e:
                    self.logger.warning('failed to check {0} for args {1}'.format(self.module.__name__, args))
                    self.logger.exception(e)
                    result = 0
                results.append(result)
        return results

    def execute_shell(self, args_combinations):
        result = []
        for args in args_combinations:
            cmd = self.command
            for i in range(10):
                cmd = cmd.replace('${0}'.format(i+1), i < len(args) and args[i] or '')
            self.logger.debug('invoking shell: {0}'.format(cmd))
            proc = subprocess.Popen(cmd, cwd=self.bin_dir, stdout=subprocess.PIPE, shell=True)
            result.append(proc.communicate()[0].split('\n', 1)[0])
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd)
        return result

    def update(self, added_items, removed_items):
        for i in added_items + removed_items:
            assert i.script == self, 'trying to insert item with different script to CoupledItem: {0} != {1}'.format(i.script, self)
        if added_items or removed_items:
            self.logger.debug('added items: {0}; removed items: {1}'.format(', '.join(map(str, added_items)), ', '.join(map(str, removed_items))))
        self.items |= set(added_items)
        self.items -= set(removed_items)
        if self.items:
            self.interval = min(self.items, key=lambda i: i.interval).interval
            self.logger.info('check interval {0} seconds'.format(self.interval))

        if self.items and not self.check_job.started:
            self.logger.debug('starting check loop')
            self.check_job.start()
        elif not self.items and self.check_job.started:
            self.logger.debug('stopping check loop')
            self.check_job.kill(block=True)

    def check_loop(self):
        while True:
            try:
                assert len(self.items) > 0
                self.check()
                assert type(self.interval) is float, 'type of interval is {0}'.format(type(self.interval))
            except Exception, e:
                self.logger.exception(e)
            time.sleep(self.interval)

    def check(self):
        items = self.items
        args_combinations = [i.args for i in items]
        timestamp = int(time.time())
        results = self.execute(args_combinations)
        self.sender.send_items(items, results, timestamp)

sys.path.append(Script.bin_dir)
os.environ['PATH'] = os.pathsep.join([os.environ['PATH'], Script.bin_dir])

class Item(object):
    def __init__(self, host, key, interval, script, args):
        self.host = host
        self.key = key
        self.interval = interval
        self.script = script
        self.logger = logging.getLogger(host)
        self.args = [arg == '$hostname' and host or arg for arg in args]

    def __eq__(self, other):
        return self.host == other.host and self.key == other.key and self.interval == other.interval

    def __hash__(self):
        return (self.host, self.key, self.interval).__hash__()

    def __str__(self):
        return self.key

class Host(object):
    def __init__(self, name, options, scripts,sender):
        self.name = name
        self.update_interval = options.update_interval
        self.scripts = scripts
        self.logger = logging.getLogger(name)
        self.items = set()
        self.sender = sender

    def update_loop(self):
        while True:
            self.update_active_checks()
            time.sleep(self.update_interval)
        gevent.joinall([i.job for i in self.items])

    item_re = re.compile('^((.+?)(\[(.+)\])?)$')
    def update_active_checks(self):
        try:
            self.logger.debug('updating item list')
            retrieved_items = set()
            for raw_key, interval in self.sender.get_active_checks(self.name):
                key, bare_key, args = self.item_re.match(raw_key).group(1, 2, 4)
                for script in self.scripts:
                    if script.key == bare_key:
                       retrieved_items.add(Item(self.name, key, interval, script, args and args.split(',') or []))
                       break
            self.logger.debug('retrieved items: {0}'.format(', '.join(map(str, retrieved_items))))
            added_items = retrieved_items - self.items
            removed_items = self.items - retrieved_items
            self.items -= removed_items
            self.items |= added_items
            if added_items:
                self.logger.info('added items: {0}'.format(', '.join(map(str, added_items))))
            if removed_items:
                self.logger.info('removed items: {0}'.format(', '.join(map(str, removed_items))))

            for script in self.scripts:
                script.update([item for item in added_items if item.script == script], [item for item in removed_items if item.script == script])

            if not self.items:
                self.logger.info('no items')
        except Exception, e:
            self.logger.exception(e)#, 'failed to update active checks list')

class Agent(object):
    def __init__(self):
        self.scripts = []
        self.coupled_items = []
        self.logger = logging.getLogger()
        self.load_config()
        self.sender = Sender(self.options)
        self.load_zabbix_configs()
        self.hosts = [Host(id, self.options, self.scripts, self.sender) for id in self.get_ldap_ids(self.options.ldap)]

    def get_sleep_time(self):
        return self.sleep_time

    def load_config(self):
        parser = config.config_parser('zabbix-agent-ng')
        parser.add_argument('--update-interval', type=int, default=120, help='items update interval')
        parser.add_argument('--server', help='zabbix feeder server')
        parser.add_argument('--port', type=int, default=10051, help='zabbix feeder port')
        parser.add_argument('--ldap', default='ldap://localhost', help='address of LDAP server with tunnels info')
        parser.add_argument('--pid-file', default='/var/run/zabbix-agent-ng.pid', help='path to pid file')
        parser.add_argument('--zabbix-conf-dir', default='/etc/zabbix', help='path to zabbix config')
        parser.add_argument('--daemonize', type=int, default=0, help='daemonize after start')
        parser.add_argument('--stop', type=int, default=0, help='stop after start')
        parser.add_argument('--protocol', default='1.8', help='feeder protocol version')
        parser.parse()
        self.options = parser.options
        parser.init_logging()

    def get_ldap_ids(self, host):
        l = ldap.initialize(host)
        for dn, entry in l.search_s('dc=local,dc=net', ldap.SCOPE_SUBTREE, 'cn=homer*'):
            yield entry['cn'][0]
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
            self.scripts.append(Script(line, self.sender))
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
        if self.options.stop:
            os.kill(os.getpid(), signal.SIGSTOP)
        if self.options.daemonize:
            self.daemonize()
        setproctitle('zabbix-agent-ng')
        waiter = Event()
        waiter.clear()
        gevent.signal(signal.SIGTERM, waiter.set)
        jobs = [gevent.spawn(host.update_loop) for host in self.hosts]
        waiter.wait()
        self.logger.info('exiting')

if __name__ == '__main__':
    a = Agent()
    a.run()
