#!/usr/bin/python
import sys
sys.path.append('/usr/local/lib/python2.6/dist-packages')
import zabbix_api

if __name__ == '__main__':
    api = zabbix_api.YaZabbixApi(server='ztop.yandex-team.ru')
    eval(sys.argv[1], locals())
