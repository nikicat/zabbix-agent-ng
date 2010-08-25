#!/usr/bin/env python

from distutils.core import setup
setup(name='zabbix-agent-ng',
      version='1.0',
      py_modules=['zabbix_agent_ng'],
      data_files=[('/etc/init.d', ['zabbix-agent-ng'])]
      )
