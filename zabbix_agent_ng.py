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
from setproctitle import setproctitle

class trapper(object):
    def __init__(self, host, server, port):
        self.host = host
        self.server = server
        self.port = port
        self.logger = logging.getLogger(host)

    def _do_request(self, data):
        sock = socket.socket()
        sock.connect((self.server, self.port))
        sock.send(data)
        return sock.makefile()

    def update_item(self, key, data):
        self.logger.debug('updating item {0}={1}'.format(key, data))
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
            self.logger.debug('received active check {0}'.format(line[:-1]))
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
        logging.debug('calling {0}.main({1})'.format(self.module_main.__module__, ', '.join(args)))
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
    def __init__(self, host, key, interval, script, args, trapper):
        self.key = key
        self.interval = interval
        self.script = script
        self.logger = logging.getLogger(host)
        self.args = [arg == '$hostname' and host or arg for arg in args]
        self.trapper = trapper

    def __eq__(self, other):
        return self.key == other.key and self.interval == other.interval

    def __hash__(self):
        return (self.key, self.interval).__hash__()

    def __str__(self):
        return self.key

    def check_loop(self):
        while True:
            try:
                self.check()
            except Exception, e:
                self.logger.exception(e)
            time.sleep(self.interval)

    def check(self):
        self.logger.debug('executing script {0}[{1}]'.format(self.script.key, ', '.join(self.args)))
        result = self.script.execute(*self.args)
        self.logger.debug('result of script {0}[{1}] = {2}'.format(self.script.key, ', '.join(self.args), result))
        self.trapper.update_item(self.key, result)

class host(object):
    def __init__(self, name, options, scripts):
        self.name = name
        self.update_interval = options.update_interval
        self.scripts = scripts
        self.logger = logging.getLogger(name)
        self.trapper = trapper(name, options.server, options.port)
        self.items = set()

    def loop(self):
        while True:
            self.update_active_checks()
            time.sleep(self.update_interval)
        gevent.joinall([i.job for i in self.items])

    item_re = re.compile('^((.+?)(\[(.+)\])?)$')
    def update_active_checks(self):
        try:
            self.logger.debug('updating item list')
            items = set()
            for raw_key, interval in self.trapper.get_active_checks():
                key, bare_key, args = self.item_re.match(raw_key).group(1, 2, 4)
                for script in self.scripts:
                    if script.key == bare_key:
                       items.add(item(self.name, key, interval, script, args and args.split(',') or [], self.trapper))
                       break
            added_items = items - self.items
            removed_items = self.items - items
            if added_items:
                self.logger.info('added items: {0}'.format(', '.join(map(str, added_items))))
            if removed_items:
                self.logger.info('removed items: {0}'.format(', '.join(map(str, removed_items))))
            gevent.killall([i.job for i in removed_items], block=True)
            self.items -= removed_items
            for i in added_items:
                i.job = gevent.spawn(i.check_loop)
            self.items |= added_items
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
