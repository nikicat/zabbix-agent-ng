#!/usr/bin/env python

from distutils.core import setup
setup(name='zabbix-agent-ng',
      version='1.0',
      description='Zabbix monitoring system agent',
      author='Nikolay Bryskin',
      scripts=['zabbix-agent-ng'],
      py_modules=['zabbix_agent_ng'],
      data_files=[('/etc', ['zabbix-agent-ng.conf']),
                  ('/etc/zabbix/bin', ['zbx_netif.py', 'zbx_calc.py', 'zbx_cpuload.py', 'zbx_cpuutil.py', 'zbx_routecache.py', 'zbx_slabinfo.sh', 'zbx_netstat.py', 'zbx_df.py', 'zbx_mem.py'])]
      )
