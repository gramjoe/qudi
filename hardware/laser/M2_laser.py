# -*- coding: utf-8 -*-
"""
This module controls an M squared laser
Originally taken from:
https://github.com/AlexShkarin/pyLabLib/blob/master/pylablib/aux_libs/devices/M2.py (I think)
Modifications by Graham Joe, M. Chalupnik
"""

from core.module import Base, ConfigOption
from interface.m2_laser_interface import M2LaserInterface
from interface.simple_laser_interface import LaserState
from interface.simple_laser_interface import ShutterState
from interface.simple_laser_interface import ControlMode

import serial
import time
import socket
import json
import websocket


class M2Laser(Base, M2LaserInterface):
#class M2Laser(): #for debugging

    #copy and paste the following to the console when debugging:
    """
from hardware.laser import M2_laser as m2
laser = m2.M2Laser()
laser._ip = '10.243.43.58'
laser._port = 39933
laser._timeout = 5
laser.on_activate()

laser.setup_terascan("medium", (750, 751), 10E9)

laser.get_laser_state()
laser.start_terascan("medium")
laser.stop_terascan("medium")"""

    """ Implements the M squared laser.

        Example config for copy-paste:

        m2_laser:
            module.Class: 'laser.M2_laser.M2Laser'
            ip: '10.243.43.58'
            port: 39933
        """

    _modclass = 'm2laserhardware'
    _modtype = 'hardware'

    _ip = ConfigOption('ip', missing='error')
    _port = ConfigOption('port', missing='error')
    _timeout = ConfigOption('port', 5, missing='warn') #good default setting for timeout?

    buffersize = 1024

    #def __init__(self, ip=_ip, port = _port, timeout=5):
    #    # for now use '10.243.43.58' and 39933
    #    self.address = (ip, port)
    #    self.timeout = timeout
    #    self.transmission_id = 1
    #    self._last_status = {}

    def on_activate(self):
        """ Initialization performed during activation of the module (like in e.g. mw_source_dummy.py)
        """

        self.address = (self._ip, self._port)
        self.timeout = self._timeout
        self.transmission_id = 1
        self._last_status = {}

        print('connecting to laser')
        self.connect_laser()
        print('connecting to wavemeter')
        self.connect_wavemeter()

    def on_deactivate(self):
        """ Deactivate module.
        """
        print('in hardware trying to deactivate')
     #   self.disconnect_wavemeter() #Not ideal to comment this, but for now, an issue with is_wavemeter_connected
                                    #is causing a crash. Specifically, a problem with _read_websocket_status
                                    #which is trying to create a websocket connection which is trying to _open_socket
                                    #but instead I get WinError 10061 No connection could be made because the target machien
                                    #actively refused it
        self.disconnect_laser()

    def connect_laser(self):
        """ Connect to Instrument.

        @return bool: connection success
        """
        self.socket = socket.create_connection(self.address, timeout=self.timeout)
        interface = self.socket.getsockname()[0]
        _, reply = self.send('start_link', {'ip_address': interface})
        if reply[-1]['status'] == 'ok':
            return True
        else:
            return False

    def disconnect_laser(self):
        """ Close the connection to the instrument.
        """
        self.socket.close()
        self.socket = None

    def set_timeout(self, timeout): #????Look at
        """ Sets the timeout in seconds for connecting or sending/receiving

        :param timeout: timeout in seconds
        """
        self.timeout = timeout
        self.socket.settimeout(timeout)

    def send(self, op, parameters, transmission_id=None): #LOOK AT
        """ Send json message to laser

        :param op: operation to be performed
        :param parameters: dictionary of parameters associated with op
        :param transmission_id: optional transmission id integer
        :return: reply operation dictionary, reply parameters dictionary
        """
        message = self._build_message(op, parameters, transmission_id)
        self.socket.sendall(message.encode('utf-8'))
        reply = self.socket.recv(self.buffersize)
        #self.log(reply)
        op_reply, parameters_reply = self._parse_reply(reply)
        self._last_status[self._parse_report_op(op_reply[-1])] = parameters_reply[-1]
        return op_reply, parameters_reply

    def set(self, setting, value, key_name='setting'): #LOOK AT
        """ Sets a laser parameter

        :param setting: string containing the setting to be set
        :param value: value of the setting
        :param key_name: optional keyword
        :return bool: set success
        """
        parameters = {}
        parameters[key_name] = value
        if key_name == 'setting':
            parameters[key_name] = [parameters[key_name]]
        _, reply = self.send(setting, parameters)
        if reply['status'] == 'ok':
            return True
        else:
            return False

    def get(self, setting): #LOOK AT
        """ Gets a laser parameter

        :param setting: string containing the setting
        :return bool: get success
        """
        _, reply = self.send(setting, {})
        if reply[-1]['status'] == 'ok':
       # if reply['status'] == 'ok':
            return reply
        else:
            return None

    def flush(self, bits=1000000):
        """ Flush read buffer
        May cause socket timeout if used when laser isn't scanning
        """

        self.set_timeout(5) #if this is lower than 5 (e.g. 2) things crash
        try:
            report = self.socket.recv(bits)
        except:
            return -1
        return report

    def update_reports(self, timeout=0.):
        """Check for fresh operation reports."""
        timeout = max(timeout, 0.001) #?
        self.socket.settimeout(timeout)
        try:
            report = self.socket.recv(self.buffersize)
        except:
            pass
            # self.log.warning("received reply while waiting for a report: '{}'".format(report[0]))
        self.socket.settimeout(self.timeout)

    def get_last_report(self, op):
        """Get the latest report for the given operation"""
        rep = self._last_status.get(op, None)
        if rep:
            return "fail" if rep["report"][0] else "success"
        return None

    def check_report(self, op):
        """Check and return the latest report for the given operation"""
        self.update_reports()
        return self.get_last_report(op)

    def wait_for_report(self, op, timeout=None):
        """Waits for a report on the given operation

        :param op string: Operation waited on
        :param timeout float: Time before operation quits
        :return dict: report
        """
        self.socket.settimeout(timeout) #modified
        while True:
            report = self.socket.recv(10000)
            op_reports, parameters_reports = self._parse_reply(report)
            for op_report, parameters_report in zip(op_reports, parameters_reports):
 #               print(op_report)
 #               print(parameters_report)

                if not self._is_report_op(op_report):
                    pass
                    #self.log.warning("received reply while waiting for a report")
                if op_report == self._make_report_op(op):
                    return parameters_report



    def connect_wavemeter(self, sync=True):
        """ Connect to the wavemeter via websocket, if sync==True wait until the connection is established

        :param sync bool: wait until connection is established
        :return bool: connection success
        """
        if self.is_wavemeter_connected():
            return True
        self._send_websocket_request('{"message_type":"task_request","task":["start_wavemeter_link"]}')
        if sync:
            while not self.is_wavemeter_connected():
                time.sleep(0.02)
        return self.is_wavemeter_connected()

    def disconnect_wavemeter(self, sync=True):
        """ Disconnect the wavemeter websocket, if sync==True wait until the connection is established

        :param sync bool: wait until connection is established
        :return: connection success
        """
        if not self.is_wavemeter_connected():
            return True
        if not sync:
            self._send_websocket_request('{"message_type":"task_request","task":["job_stop_wavemeter_link"]}')
        else:
            while self.is_wavemeter_connected():
                #self.stop_all_operaion()
                #self.lock_wavemeter(False)
                #if self.is_wavemeter_lock_on():
                #   time.sleep(1.)
                self._send_websocket_request('{"message_type":"task_request","task":["job_stop_wavemeter_link"]}')
                for _ in range(25):
                    if not self.is_wavemeter_connected():
                        return True
                    time.sleep(0.02)
            return self.is_wavemeter_connected()

    def is_wavemeter_connected(self):
        """ Checks if the wavemeter is connected via websocket

        :return bool: wavemeter connected via websocket
        """
        return bool(self._read_websocket_status(present_key="wlm_fitted")["wlm_fitted"])

    def get_laser_state(self):
        """ Gets the state of the laser.

        :return dict: laser state message
        """
        _, reply = self.send('get_status', {})
        return reply[-1]

    def get_full_tuning_status(self):
        """ Gets the current wavelength, lock_status, and extended zone of the laser

        :return dict: laser tuning status
        """

        _, reply = self.send('poll_wave_m', {})
        return reply[-1]

    def lock_wavemeter(self, lock=True, sync=True):
        """Causes SolsTiS to monitor the wavelength and automatically readjust the tuning to the currently set target
        wavelength.

        :param lock bool: Lock the etalon
        :param sync bool: Wait for the etalon to lock
        :return bool: Wavemeter locked
        """

        _, reply = self.send('lock_wave_m', {'operation': 'on' if lock else 'off'})
        if sync:
            while self.is_wavemeter_lock_on() != lock:
                time.sleep(0.05)
        return self.is_wavemeter_lock_on()

    def is_wavemeter_lock_on(self):
        """Check if the laser is locked to the wavemeter

        :return bool: Wavemeter locked
        """
        return bool(self.get_full_tuning_status()["lock_status"][0])

    def tune_wavelength(self, wavelength, sync=True, timeout=None):
        """
        Fine-tune the wavelength. Only works if the wavemeter is connected.

        :param wavelength float: Wavelength (nm) to be tuned to
        :param sync bool: Wait for the etalon to lock
        :param timeout: timeout in seconds
        :return dict: Tuning report
        """
        _, reply = self.send("set_wave_m", {"wavelength": [wavelength], "report": "finished"})
        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning("can't tune wavelength: no wavemeter link")
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't tune wavelength: {}nm is out of range".format(wavelength * 1E9))
        if sync:
            return self.wait_for_report('set_wave_m', timeout=timeout)

    def check_tuning_report(self):
        """Check wavelength fine-tuning report

        :return: 'success' or 'fail' if the operation is complete, or None if it is still in progress.
        """
        return self.check_report("set_wave_m_r")

    def wait_for_tuning(self, timeout=None):
        """Wait until wavelength fine-tuning is complete

        :return dict: Tuning report
        """
        self.wait_for_report("set_wave_m", timeout=timeout)

    def get_tuning_status(self):
        """ Get fine-tuning status.

        :reutn string: Tuning status
            'idle': no tuning or locking
            'nolink': no wavemeter link
            'tuning': tuning in progress
            'locked: tuned and locked to the wavemeter
        """
        status = self.get_full_tuning_status()["status"][0]
        return ["idle", "nolink", "tuning", "locked"][status]

    def get_wavelength(self):
        """
        Get fine-tuned wavelength.

        Only works if the wavemeter is connected.
        """
        return self.get_full_tuning_status()["current_wavelength"][0]

    def get_terascan_wavelength(self):
        #use this function to get the wavelength while terascan is running
        #currently calls to this function take ~.21 sec
        timeouted = self.flush(1000000)
        #TODO: try experimenting with not using flush, to decrease time it takes to call this function

        if timeouted == -1: #timeout or some other error in flush()
            #assume this means the scan is done, even though there are other possible reasons for this to occur
            #(eg. bad connection)
            print('Timeout in get_terascan_wavelength')
            return -1, 'complete'

        out = self.get_laser_state()

        if out.get('report'): #I think this means we happened to land on the report end status update
            #(unlikely since we are constantly grabbing one update out of many)
            #print(out)
            return -1, 'complete'


        if out.get('activity'):
            status = out['activity']
        else:
            status = 'stitching'
            #print(out)

        return out['wavelength'][0], status

    def get_terascan_wavelength_web(self):
        #Currently does not work very well
        #uses websocket instead of tcp socket to get wavelength
        #calls take ~0.18 sec, not appreciably faster
    #    while True:
    #        try:
    #            msg_data = self._read_websocket_status_leftpanel()
    #            break
    #        except:
    #            time.sleep(0.05)

        try:
            msg_data = self._read_websocket_status_leftpanel()
        except:
            print('Scan completed')
            return -1, 'complete'

        if msg_data['dodgy_reading']:
            status = 'stitching'
        else:
            status = 'scanning'
        return msg_data['wlm_wavelength'], status


    def stop_tuning(self):
        """Stop fine wavelength tuning."""
       # _, reply = self.send("stop_wave_m", {})
        _, reply = self.send("stop_move_wave_t", {})
       # print(reply)
        if reply[-1]["status"][0] == 1:
            print('-1')
            #self.log.warning("can't stop tuning: no wavemeter link")

    def tune_wavelength_table(self, wavelength, sync=True):
        """Coarse-tune the wavelength. Only works if the wavemeter is disconnected.

        :param wavelength double: Wavelength (nm) to be tuned to
        :param sync bool: Wait for the etalon to lock
        """
        _, reply = self.send("move_wave_t", {"wavelength": [wavelength], "report": "finished"})
        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning("can't tune etalon: command failed")
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't tune wavelength: {}nm is out of range".format(wavelength * 1E9))
        if sync:
            self.wait_for_report("move_wave_t")

    def get_full_tuning_status_table(self):
        """Get full coarse-tuning status (see M2 ICE manual for 'poll_move_wave_t' command)"""
        return self.send("poll_move_wave_t", {})[1]

    def get_tuning_status_table(self):
        """Get coarse-tuning status.

        :return string:
            'done': tuning is done
            'tuning': tuning in progress
            'fail': tuning failed
        """
        status = self.get_full_tuning_status_table()["status"][0]
        return ["done", "tuning", "fail"][status]

    def get_wavelength_table(self):
        """
        Get course-tuned wavelength.

        Only works if the wavemeter is disconnected.
        """
        return self.get_full_tuning_status_table()["current_wavelength"][0]

    def stop_tuning_table(self):
        """Stop coarse wavelength tuning."""
        self.send('stop_move_wave_t', {})

    def tune_etalon(self, percent, sync=True):
        """Tune the etalon to percent. Only works if the wavemeter is disconnected.

        :param percent float: Percent to tune etalon to
        :param sync bool: Wait for the etalon to tune
        """
        _, reply = self.send("tune_etalon", {"setting": [percent], "report": "finished"})
        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning("can't tune etalon: {} is out of range".format(percent))
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't tune etalon: command failed")
        if sync:
            return self.wait_for_report("tune_etalon")

    def tune_laser_resonator(self, percent, fine=False, sync=True):
        """Tune the laser cavity to percent. Only works if the wavemeter is disconnected.

    :param fine bool: Fine tuning
            True: adjust fine tuning
            False: adjust coarse tuning.
    :param sync bool: Wait for the laser cavity to tune
        """
        _, reply = self.send("fine_tune_resonator" if fine else "tune_resonator", {"setting": [percent], "report": "finished"})
        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning("can't tune resonator: {} is out of range".format(perc))
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't tune resonator: command failed")
        if sync:
            self.wait_for_report("fine_tune_resonator")

    _terascan_rates = [50E3, 100E3, 200E3, 500E3, 1E6, 2E6, 5E6, 10E6, 20E6, 50E6, 100E6, 200E6, 500E6, 1E9, 2E9, 5E9,
                       10E9, 15E9, 20E9, 50E9, 100E9]

    def setup_terascan(self, scan_type, scan_range, rate, trunc_rate=True):
        """
        Setup terascan.

        :param scan_type str: scan type
            'medium': BRF+etalon, rate from 100 GHz/s to 1 GHz/s
            'fine': All elements, rate from 20 GHz/s to 1 MHz/s
            'line': All elements, rate from 20 GHz/s to 50 kHz/s).
        :param scan_range tuple: (start,stop) in nm
        :param rate float: scan rate in Hz/s
        :param trunc_rate bool: Truncate rate
            True: Truncate the scan rate to the nearest available rate
            False: Incorrect rate would raise an error.
        """
        self._check_terascan_type(scan_type)
        if trunc_rate:
            rate = self._trunc_terascan_rate(rate)
        if rate >= 1E9:
            fact, units = 1E9, "GHz/s"
        elif rate >= 1E6:
            fact, units = 1E6, "MHz/s"
        else:
            fact, units = 1E3, "kHz/s"
        parameters = {"scan": scan_type, "start": [scan_range[0]], "stop": [scan_range[1]],
                  "rate": [rate / fact], "units": units}
        _, reply = self.send('scan_stitch_initialise', parameters)

        if not reply[-1].get('status'):
            print(reply)
            return 2 #error!

        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning("can't setup TeraScan: start ({:.3f} THz) is out of range".format(scan_range[0] / 1E12))
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't setup TeraScan: stop ({:.3f} THz) is out of range".format(scan_range[1] / 1E12))
        elif reply[-1]["status"][0] == 3:
            pass
            #self.log.warning("can't setup TeraScan: scan out of range")
        elif reply[-1]["status"][0] == 4:
            pass
            #self.log.warning("can't setup TeraScan: TeraScan not available")
        return reply[-1]

    def start_terascan(self, scan_type, sync=True, sync_done=True):
        """Start terascan.
        :param scan_type string: Scan type
            'medium': BRF+etalon, rate from 100 GHz/s to 1 GHz/s
            'fine': All elements, rate from 20 GHz/s to 1 MHz/s
            'line': All elements, rate from 20 GHz/s to 50 kHz/s
        :param sync bool: Wait until the scan is set up (not until the whole scan is complete)
        :param sync_done bool: wait until the whole scan is complete
        """
        self._check_terascan_type(scan_type)
        if sync:
            self.enable_terascan_updates()
        _, reply = self.send("scan_stitch_op", {"scan": scan_type, "operation": "start", "report": "finished"})
        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning(("can't start TeraScan: operation failed")
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't start TeraScan: TeraScan not available")
#        print(reply)

        if sync:
            self.wait_for_terascan_update()
#        if sync_done:
#            self.wait_for_report("scan_stitch_op") #Prints to command line, also returns the same thing

    _terascan_update_op = "wavelength"

    def enable_terascan_updates(self, enable=True, update_period=0):
        """Enable sending periodic terascan updates. Laser will send updates in the beginning and in the end of every terascan segment.

        :param enable bool: Enable terascan updates
        :param update_period float: Sends updates every update_period percents of the segment (this option doesn't seem to be working currently).
        """
        _, reply = self.send("scan_stitch_output", {"operation": ("start" if enable else "stop"), "update": [update_period]})
        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning("can't setup TeraScan updates: operation failed")
        if reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't setup TeraScan updates: incorrect update rate")
        if reply[-1]["status"][0] == 3:
            pass
            #self.log.warning("can't setup TeraScan: TeraScan not available")
        self._last_status[self._terascan_update_op] = None

    def check_terascan_update(self):
        """Check the latest terascan update.

        :return dict: Terascan report {'wavelength': current_wavelength, 'operation': op}
        where op is:
            'scanning': scanning in progress
            'stitching': stitching in progress
            'finished': scan is finished
            'repeat': segment is repeated
        """
        self.update_reports()
        rep = self._last_status.get(self._terascan_update_op, None)
        return rep

    def wait_for_terascan_update(self):
        """Wait until a new terascan update is available
        
        :return dict: Terascan report
        """
        self.wait_for_report(self._terascan_update_op)
        return self.check_terascan_update()

    def check_terascan_report(self):
        """Check report on terascan start.
        
        :return: 'success' or 'fail' if the operation is complete, or None if it is still in progress
        """
        return self.check_report("scan_stitch_op")

    def stop_terascan(self, scan_type, sync=False):
        """Stop terascan of the given type.
        
        :param scan type string: Scan type
            'medium': BRF+etalon, rate from 100 GHz/s to 1 GHz/s
            'fine': All elements, rate from 20 GHz/s to 1 MHz/s
            'line': All elements, rate from 20 GHz/s to 50 kHz/s
        :param sync_done bool: wait until the scan stop is complete.
        """
        self._check_terascan_type(scan_type)

        #Using TCP connection (below) works, but is very slow
        #_, reply = self.send("scan_stitch_op", {"scan": scan_type, "operation": "stop"})
        #print(reply)

        #FASTER WAY: Seems is already here via stop_scan_web - look into
        self._send_websocket_request(
            '{"stop_scan_stitching":1,"message_type":"page_update"}'
        )
        self._send_websocket_request(
            '{"message_type":"task_request","task":["medium_scan_stop"]}')
        if sync:
           # ready = 0
           # while ready != -1:
           #     ready = self.flush() #waste of 5 seconds
            while True:
                try:
                    self.on_activate() #todo fix so on_activate isn't necessary
                    return
                except:
                    pass
            ##self.wait_for_report("scan_stitch_op")

    _web_scan_status_str = ['off', 'cont', 'single', 'flyback', 'on', 'fail']

    def get_terascan_status(self, scan_type, web_status="auto"):
        """Get status of a terascan of a given type.
        
        :param scan type string: Scan type
            'medium': BRF+etalon, rate from 100 GHz/s to 1 GHz/s
            'fine': All elements, rate from 20 GHz/s to 1 MHz/s
            'line': All elements, rate from 20 GHz/s to 50 kHz/s
        :param web_status: Don't really know what this is
        :return dict: Dictionary with 4 items:
            'current': current laser frequency
            'range': tuple with the fill scan range
            'status': Laser status
                'stopped': Scan is not in progress)
                'scanning': Scan is in progress
                'stitching': Scan is in progress, but currently stitching
            'web': Where scan is running in web interface (some failure modes still report 'scanning' through the usual interface);
            only available if the laser web connection is on.
        """
        self._check_terascan_type(scan_type)
        _, reply = self.send("scan_stitch_status", {"scan": scan_type})
        status = {}
        if reply[-1]["status"][0] == 0:
            status["status"] = "stopped"
            status["range"] = None
        elif reply[-1]["status"][0] == 1:
            if reply[-1]["operation"][0] == 0:
                status["status"] = "stitching"
            elif reply[-1]["operation"][0] == 1:
                status["status"] = "scanning"
            status["range"] = reply[-1]["start"][0], reply[-1]["stop"][0]
            status["current"] = reply[-1]["current"][0] if reply[-1]["current"][0] else 0
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't stop TeraScan: TeraScan not available")
        web_status = self._as_web_status(web_status)
        if web_status:
            status["web"] = self._web_scan_status_str[web_status["scan_status"]]
        else:
            status["web"] = None
        return status

    _fast_scan_types = {"cavity_continuous", "cavity_single", "cavity_triangular",
                        "resonator_continuous", "resonator_single", "resonator_ramp", "resonator_triangular",
                        "ect_continuous", "ecd_ramp",
                        "fringe_test"}

    def start_fast_scan(self, scan_type, width, time, sync=False, setup_locks=True):
        """Setup and start fast scan.

        :param scan_type str: scan type(see ICE manual for details)
            'cavity_continuous'
            'cavity_single'
            'cavity_triangular'
            'resonator_continuous'
            'resonator_single'
            'resonator_ramp'
            'resonator_triangular'
            'ect_continuous'
            'ecd_ramp'
            'fringe_test'
        :param width float: scan width (in GHz)
        :param time float: scan time/period (in s)
        :param sync bool: Wait until the scan is set up (not until the whole scan is complete)
        :param setup_locks bool: Automatically setup etalon and reference cavity locks in the appropriate states.
        """
        self._check_fast_scan_type(scan_type)
        if setup_locks:
            if scan_type.startswith("cavity"):
                self.lock_etalon()
                self.lock_reference_cavity()
            elif scan_type.startswith("resonator"):
                self.lock_etalon()
                self.unlock_reference_cavity()
        _, reply = self.send("fast_scan_start", {"scan": scan_type, "width": [width], "time": [time]})
        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning(("can't start fast scan: width too great for the current tuning position")
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't start fast scan: reference cavity not fitted")
        elif reply[-1]["status"][0] == 3:
            pass
            #self.log.warning("can't start fast scan: ERC not fitted")
        elif reply[-1]["status"][0] == 4:
            pass
            #self.log.warning("can't start fast scan: invalid scan type")
        elif reply[-1]["status"][0] == 5:
            pass
            #self.log.warning("can't start fast scan: time >10000 seconds")
        if sync:
            self.wait_for_report("fast_scan_start")

    def check_fast_scan_report(self):
        """Check fast scan report.

        :return: 'success' or 'fail' if the operation is complete, or None if it is still in progress.
        """
        return self.check_report("fast_scan_start")

    def stop_fast_scan(self, scan_type, return_to_start=True, sync=False):
        """Stop fast scan of the given type.

        :param scan_type str: scan type(see ICE manual for details)
            'cavity_continuous'
            'cavity_single'
            'cavity_triangular'
            'resonator_continuous'
            'resonator_single'
            'resonator_ramp'
            'resonator_triangular'
            'ect_continuous'
            'ecd_ramp'
            'fringe_test'
        :param return_to_start bool: Return to start.
            True: Return to the center frequency after stopping
            False: Stay at the current instantaneous frequency.
        :param sync bool: Wait until the operation is complete.
        """
        self._check_fast_scan_type(scan_type)
        _, reply = self.send("fast_scan_stop" if return_to_start else "fast_scan_stop_nr", {"scan": scan_type})
        if reply[-1]["status"][0] == 1:
            pass
            #self.log.warning("can't stop fast scan: operation failed")
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't stop fast scan: reference cavity not fitted")
        elif reply[-1]["status"][0] == 3:
            pass
            #self.log.warning("can't stop fast scan: ERC not fitted")
        elif reply[-1]["status"][0] == 4:
            pass
            #self.log.warning("can't stop fast scan: invalid scan type")
        if sync:
            self.wait_for_report("fast_scan_stop")

    def get_fast_scan_status(self, scan_type):
        """Get status of a fast scan of a given type.

        :param scan_type str: scan type(see ICE manual for details)
            'cavity_continuous'
            'cavity_single'
            'cavity_triangular'
            'resonator_continuous'
            'resonator_single'
            'resonator_ramp'
            'resonator_triangular'
            'ect_continuous'
            'ecd_ramp'
            'fringe_test'
        "return dict: A dictionary with 4 items:
            'status': 'stopped' - scan is not in progress, or 'scanning' - scan is in progress
            'value': current tuner value (in percent)
        """
        self._check_fast_scan_type(scan_type)
        _, reply = self.query("fast_scan_poll", {"scan": scan_type})
        status = {}
        if reply[-1]["status"][0] == 0:
            status["status"] = "stopped"
        elif reply[-1]["status"][0] == 1:
            status["status"] = "scanning"
        elif reply[-1]["status"][0] == 2:
            pass
            #self.log.warning("can't poll fast scan: reference cavity not fitted")
        elif reply[-1]["status"][0] == 3:
            pass
            #self.log.warning("can't poll fast scan: ERC not fitted")
        elif reply[-1]["status"][0] == 4:
            pass
            #self.log.warning("can't poll fast scan: invalid scan type")
        else:
            pass
            #self.log.warning("can't determine fast scan status: {}".format(reply["status"][0]))
        status["value"] = reply[-1]["tuner_value"][0]
        return status

    def stop_scan_web(self, scan_type):
        """Stop scan of the current type (terascan or fine scan) using web interface. More reliable than native
        programming interface, but requires activated web interface.

        :param scan_type str: scan type(see ICE manual for details)
            'cavity_continuous'
            'cavity_single'
            'cavity_triangular'
            'resonator_continuous'
            'resonator_single'
            'resonator_ramp'
            'resonator_triangular'
            'ect_continuous'
            'ecd_ramp'
            'fringe_test'
        """
        if not self.use_websocket:
            return
        try:
            self._check_terascan_type(scan_type)
            scan_type = scan_type + "_scan"
        except:
            self._check_fast_scan_type(scan_type)
            scan_type = scan_type.replace("continuous", "cont")
        scan_task = scan_type + "_stop"
        print(scan_task) #check
        self._send_websocket_request('{{"message_type":"task_request","task":["{}"]}}'.format(scan_task))

    _default_terascan_rates = {"line": 10E6, "fine": 100E6, "medium": 5E9}

    def stop_all_operation(self, repeated=True):
        """Stop all laser operations (tuning and scanning). More reliable than native programming interface, but
        requires activated web interface.
        TODO: Implement countdown so that this function works

        :param: repeated bool: Repeat trying to stop the operations until succeeded (more reliable, but takes more time).\
        :return bool: If the operation is a success
        """
        attempts = 0
        #ctd = general.Countdown(self.timeout or None)
        ctd = self.timeout
        while True:
            operating = False
            if not (self.use_websocket and self.get_full_web_status()["scan_status"] == 0):
                for scan_type in ["medium", "fine", "line"]:
                    stat = self.get_terascan_status(scan_type)
                    if stat["status"] != "stopped":
                        operating = True
                        self.stop_terascan(scan_type)
                        time.sleep(0.5)
                        if attempts > 3:
                            self.stop_scan_web(scan_type)
                        if attempts > 6:
                            rate = self._default_terascan_rates[scan_type]
                            scan_center = stat["current"] or 400E12
                            self.setup_terascan(scan_type, (scan_center, scan_center + rate * 10), rate)
                            self.start_terascan(scan_type)
                            time.sleep(1.)
                            self.stop_terascan(scan_type)
                for scan_type in self._fast_scan_types:
                    try:
                        if self.get_fast_scan_status(scan_type)["status"] != "stopped":
                            operating = True
                            self.stop_fast_scan(scan_type)
                            time.sleep(0.5)
                            if attempts > 3:
                                self.stop_scan_web(scan_type)
                    except M2Error:
                        pass
            if self.get_tuning_status() == "tuning":
                operating = True
                self.stop_tuning()
            if self.get_tuning_status_table() == "tuning":
                operating = True
                self.stop_tuning_table()
            if (not repeated) or (not operating):
                break
            time.sleep(0.1)
            attempts += 1
            if (attempts > 10 and ctd.passed()):
                #raise M2Error("coudn't stop all operations: timed out")
                print('M2 Error: could not stop all operations')
        return not operating

    def _build_message(self, op, params, transmission_id=None):
        """ Builds a json message in standard format to be sent to the laser

        :param op: operation to be performed by the laser
        :param params: parameters dictionary associated with the operation
        :param transmission_id: optional transmission id integer
        :return: json byte string to be send to the laser
        """
        if transmission_id is None:
            self.transmission_id = self.transmission_id % 16383 + 1
        else:
            self.transmission_id = transmission_id
        message = {'message': {'transmission_id': [self.transmission_id], 'op': op, 'parameters': dict(params)}}
        return json.dumps(message)

    def _parse_messages(self, message):
        """ Parses a standard format json message into a dictionary

        :param message: json string
        :return: message dictionary
        """
  #      print(message)
#        print(len(message))  # seems messages are greater than the buffer length... not sure why

        if len(message) >= self.buffersize: #As is, this throws away data from messages on the edges of the buffer
                                        #Not sure if there is a good solution though
            msg = message.rsplit('},{', 1) #split from right
            message = msg[0]
            msg = message.split('},{', 1) #split from left
            message = msg[1]
            message = '[{'+ message + '}]'
#            print(message)


        pmessages = json.loads(message)
        for i in range(len(pmessages)):
            if 'message' not in pmessages[i]:
                pass
                #self.log.warning('coudn't decode message: {}'.format(message))
            pmessages[i] = pmessages[i]['message']
            for key in ['transmission_id', 'op', 'parameters']:
                if key not in pmessages[i]:
                    pass
                    #self.log.warning("parameter '{}' not in the message {}".format(key,msg))
        return pmessages

    _parse_errors = ["unknown", "JSON parsing error", "'message' string missing",
                     "'transmission_id' string missing", "No 'transmission_id' value",
                     "'op' string missing", "No operation name",
                     "operation not recognized", "'parameters' string missing", "invalid parameter tag or value"]

    def _parse_reply(self, reply):
        """ Parses a json reply from the laser into the two relevant dictionaries

        :param reply: json reply from laser
        :return: reply operation dictionary, reply parameters dictionary
        """
        op_reply = []
        parameters_reply = []
        reply = reply.decode("utf-8")
        preplies = self._parse_messages('[' + reply.replace('}{', '},{') + ']')
        for preply in preplies:
            if preply["op"] == "parse_fail":
                parameters = preply["parameters"]
                perror = parameters["protocol_error"][0]
                perror_description = "unknown" if perror>=len(self._parse_errors) else self._parse_errors[perror]
                error_message = "device parse error: transmission_id={}, error={}({}), error point='{}'".format(
                    parameters.get("transmission", ["NA"])[0], perror, perror_description, parameters.get("JSON_parse_error","NA"))
                #self.log.warning(error_message)
            op_reply.append(preply["op"])
            parameters_reply.append(preply["parameters"])
        return op_reply, parameters_reply

    def _is_report_op(self, op):
        return op.endswith("_f_r") or op == self._terascan_update_op

    def _make_report_op(self, op):
        return op if op == self._terascan_update_op else op + "_f_r"

    def _parse_report_op(self, op):
        return op if op == self._terascan_update_op else op[:-4]

    def _send_websocket_request(self, message):
        """ Sends a websocket request

        :param message: message to be sent
        """
        ws = websocket.create_connection("ws://{}:8088/control.htm".format(self.address[0]), timeout=self.timeout)
        try:
            self._wait_for_websocket_status(ws, present_key="wlm_fitted")
            self._wait_for_websocket_status(ws, present_key="wlm_fitted")
            ws.send(message)
        finally:
            ws.close()

    def _wait_for_websocket_status(self, ws, present_key=None, nmax=20):
        """ Waits for the websocket to respond and returns the status

        :param ws: websocket
        :param present_key: not sure, I think its the status that we are waiting or
        :param nmax: number of iterations to wait for
        :return: websocket status
        """
        full_data = {}
        for _ in range(nmax):
            data = ws.recv()
            full_data.update(json.loads(data))
            if present_key is None or present_key in data:
                return full_data

    def _read_websocket_status(self, present_key=None, nmax=20):
        """ Reads the websocket status

        :param present_key: not sure, I think its the status that we are waiting or
        :param nmax: number of iterations to wait for
        :return: websocket status
        """
        ws = websocket.create_connection("ws://{}:8088/control.htm".format(self.address[0]), timeout=self.timeout)
        try:
            return self._wait_for_websocket_status(ws, present_key=present_key, nmax=nmax)
        finally:
            ws.recv()
            ws.close()

    def _read_websocket_status_leftpanel(self, present_key=None, nmax=20):
        """ Reads the websocket status

        :param present_key: not sure, I think its the status that we are waiting or
        :param nmax: number of iterations to wait for
        :return: websocket status
        """
        print('inside read websocket status')
        ws = websocket.create_connection("ws://{}:8088/control.htm".format(self.address[0]), timeout=self.timeout)
        try:
            self._wait_for_websocket_status(ws, present_key=present_key, nmax=nmax) #first call gets first_page
            print('read websocket status ended')
            return self._wait_for_websocket_status(ws, present_key=present_key, nmax=nmax) #second call gets left_panel
        finally:
            ws.recv()
            ws.close()

        print('read websocket stautus ended')


    def _check_terascan_type(self, scan_type):
        """Checks that the terascan type is valid.

        :param scan_type string: Terascan type
        """

        if scan_type not in {"coarse", "medium", "fine", "line"}:
            pass
            #self.log.warning("unknown terascan type: {}".format(scan_type))
        if scan_type == "coarse":
            pass
            #self.log.warning("coarse scan is not currently available")

    def _trunc_terascan_rate(self, rate):
        """Chooses the closest terascan rate

        :param rate: Input terascan rate
        :return: Closest terascan rate
        """

        for tr in self._terascan_rates[::-1]:
            if rate >= tr:
                return tr
        return self._terascan_rates[0]

    def _check_fast_scan_type(self, scan_type):
        """Check that fast scan type is valid

        :param scan_type str: Fast scan type
        """
        if scan_type not in self._fast_scan_types:
            pass
            #self.log.warning("unknown fast scan type: {}".format(scan_type))


