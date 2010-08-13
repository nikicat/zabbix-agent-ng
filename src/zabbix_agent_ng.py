'''
Created on Aug 12, 2010

@author: nbryskin
'''

import socket
import base64
import struct
import os
import os.path
import re
import sys
import time
import logging
import subprocess

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
    def __init__(self, key, name):
        logging.debug('initializing script {0} {1}'.format(key, name))
        self.key = key
        self.name = name
        if name.endswith('.py'):
            module = __import__(name[:-3])
            if hasattr(module, 'main'):
                self.execute = module.main

    def execute(self, *args):
        cmd = [self.name] + list(args)
        proc = subprocess.Popen(cmd, cwd=self.bin_dir, stdout=subprocess.PIPE, shell=True)
        output = proc.communicate()[0].split('\n', 1)[0]
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, ' '.join(cmd))
        return output

sys.path.append(script.bin_dir)
os.environ['PATH'] = os.pathsep.join([os.environ['PATH'], script.bin_dir])

class item(object):
    def __init__(self, interval, script, args):
        self.script = script
        self.args = args
        self.interval = interval
        self.last_check = 0

    def check(self, host):
        self.last_check = time.time()
        logging.debug('executing script {0}[{1}, {2}]'.format(self.script.key, host, self.args))
        return self.script.execute(host, *self.args)

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

    item_re = re.compile('^(.+?)(\[(.+)\])?$')
    def update_active_checks(self):
        logging.info('updating item list for host {0}'.format(self.name))
        self.items = []
        self.last_update = time.time()
        for raw_key, interval in self.trapper.get_active_checks():
            key, args = self.item_re.match(raw_key).group(1, 3)
            for script in self.scripts:
                if script.key == key:
                    self.items.append(item(interval, script, args or [] and args.split(',')))
                    break

    def update(self, item):
        if item == self:
            self.update_active_checks()
        else:
            try:
                self.trapper.update_item(item.script.key, item.check(self.name))
            except BaseException, e:
                logging.warning('failed to update item {0}[{1}, {2}]: {3}'.format(item.script.key, self.name, item.args, e))

    def get_nearest_check(self):
        return min([self] + self.items, key=lambda x: x.get_timeout())
    
    def get_timeout(self):
        return self.update_interval - (time.time() - self.last_update)

class agent(object):
    config_dir = '/etc/zabbix/conf.d'

    def __init__(self, hosts, server, update_interval=120):
        self.scripts = []
        self.load_configs()
        self.hosts = [host(host_name, server, update_interval, self.scripts) for host_name in hosts]

    def load_configs(self):
        for name in os.listdir(self.config_dir):
            self.load_config(os.path.join(self.config_dir, name))
                
    def load_config(self, full_path):
        try:
            for line in open(full_path).readlines():
                if line.startswith('UserParameter='):
                    self.parse_config_line(line)
        except:
            logging.warning('can\'t load config file {0}'.format(full_path))

    config_re = re.compile('^UserParameter=(.+)\[\*\],(.+?) .*$')
    def parse_config_line(self, line):
        key, name = self.config_re.match(line).group(1, 2)
        try:
            self.scripts.append(script(key, name))
        except BaseException, e:
            logging.warning('can\'t parse line {0}: {1}'.format(line, e))

    def start(self):
        while True:
            self.run()

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
        current_host.update(current_item)

if __name__ == '__main__':
#    c = trapper('tunnel092', 'monitor-iva1.yandex.net')
#    c.update_item('system.uptime', 12345)
#    print('OK')
#    c.update_item('system.uptime', 12345)
#    print('OK')
#    print(c.get_active_checks())
#    c.update_item('system.uptime', 12345)
#    print('OK')
    logging.basicConfig(level=logging.DEBUG)
    a = agent(['tunnel093', 'tunnel092'], 'monitor-iva1.yandex.net')
    a.start()
