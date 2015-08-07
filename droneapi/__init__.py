import time
from pymavlink import mavutil

# Clean impl of mp dependencies for droneapi

# Cumulative list of packets we don't yet handle
# TODO: clear this list and handle them!
swallow = ['AHRS', 'AHRS2', 'ATTITUDE', 'EKF_STATUS_REPORT', 'GLOBAL_POSITION_INT',
           'GPS_RAW_INT', 'HWSTATUS', 'MEMINFO', 'MISSION_CURRENT', 'NAV_CONTROLLER_OUTPUT',
           'RAW_IMU', 'RC_CHANNELS_RAW', 'SCALED_IMU2', 'SCALED_PRESSURE', 'SENSOR_OFFSETS',
           'SERVO_OUTPUT_RAW', 'SIMSTATE', 'SYSTEM_TIME', 'SYS_STATUS', 'TERRAIN_REPORT',
           'TERRAIN_REQUEST', 'STATUSTEXT', 'LOCAL_POSITION_NED', 'COMMAND_ACK',
           'MISSION_ACK', 'VFR_HUD']

import droneapi.module.api as api

class FakeAPI:
    def __init__(self, module):
        self.__vehicle = api.MPVehicle(module)
        self.exit = False

    def get_vehicles(self, query=None):
        return [ self.__vehicle ]

# def mav_thread(conn, state):

#     return (in_queue, out_queue)

class MavWriter():
    def __init__(self, queue):
        self.queue = queue

    def write(self, pkt):
        self.queue.put(pkt)

    def read(self):
        print('WTF')
        import os
        os._exit(43)

def send_heartbeat(master):
    master.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)

def request_data_stream_send(master, rate=1):
    master.mav.request_data_stream_send(master.target_system, master.target_component,
                                        mavutil.mavlink.MAV_DATA_STREAM_ALL, rate, 1)

from Queue import Queue
from threading import Thread

class MPFakeState:
    def __init__(self, master):
        self.master = master
        out_queue = Queue()
        # self.mav_thread = mav_thread(master, self)
        # self.mav = master.mav

        self.api = None

        # TODO get rid of "master" object as exposed,
        # keep it private, expose something smaller for droneapi
        self.out_queue = out_queue
        self.master.mav = mavutil.mavlink.MAVLink(MavWriter(self.out_queue), srcSystem=self.master.source_system, use_native=False)

        self.command_map = {}
        self.completions = {}

        self.target_system = 0
        self.target_component = 0

        self.lat = None
        self.lon = None
        self.alt = None

        self.vx = None
        self.vy = None
        self.vz = None

        self.airspeed = None
        self.groundspeed = None

        self.pitch = None
        self.yaw = None
        self.roll = None
        self.pitchspeed = None
        self.yawspeed = None
        self.rollspeed = None

        self.mount_pitch = None
        self.mount_yaw = None
        self.mount_roll = None

        self.voltage = None
        self.current = None
        self.level = None

        self.rc_readback = {}

        self.last_waypoint = 0

        self.eph = None
        self.epv = None
        self.satellites_visible = None
        self.fix_type = None  # FIXME support multiple GPSs per vehicle - possibly by using componentId

        self.rngfnd_distance = None
        self.rngfnd_voltage = None

        self.status = type('MPStatus',(object,),{
            'flightmode': 'AUTO',
            'armed': False,
        })()

        self.mav_param = {} 

        # Weird
        self.mpstate = self
        self.functions = self

    def module(self, which):
        # psyche
        return self

    def param_set(self, name, value, retries=3):
        # TODO dumbly reimplement this using timeout loops
        # because we should actually be awaiting an ACK of PARAM_VALUE
        # changed, but we don't have a proper ack structure, we'll
        # instead just wait until the value itself was changed

        name = name.upper()
        value = float(value)
        success = False
        while retries > 0:
            retries -= 1
            self.master.param_set_send(name.upper(), value)
            tstart = time.time()
            while time.time() - tstart < 1:
                if self.mav_param[name] == value:
                    return True
                time.sleep(0.1)
        
        print("Timeout setting parameter %s to %f" % (name, value))
        return False

    def __on_change(self, *args):
        for a in args:
            for v in self.api.get_vehicles():
                v.notify_observers(a)

    def mavlink_packet(self, m):
        typ = m.get_type()
        if typ == 'GLOBAL_POSITION_INT':
            (self.lat, self.lon) = (m.lat / 1.0e7, m.lon / 1.0e7)
            (self.vx, self.vy, self.vz) = (m.vx / 100.0, m.vy / 100.0, m.vz / 100.0)
            self.__on_change('location', 'velocity')
        elif typ == 'GPS_RAW':
            pass # better to just use global position int
            # (self.lat, self.lon) = (m.lat, m.lon)
            # self.__on_change('location')
        elif typ == 'GPS_RAW_INT':
            # (self.lat, self.lon) = (m.lat / 1.0e7, m.lon / 1.0e7)
            self.eph = m.eph
            self.epv = m.epv
            self.satellites_visible = m.satellites_visible
            self.fix_type = m.fix_type
            self.__on_change('gps_0')
        elif typ == "VFR_HUD":
            self.heading = m.heading
            self.alt = m.alt
            self.airspeed = m.airspeed
            self.groundspeed = m.groundspeed
            self.__on_change('location', 'airspeed', 'groundspeed')
        elif typ == "ATTITUDE":
            self.pitch = m.pitch
            self.yaw = m.yaw
            self.roll = m.roll
            self.pitchspeed = m.pitchspeed
            self.yawspeed = m.yawspeed
            self.rollspeed = m.rollspeed
            self.__on_change('attitude')
        elif typ == "SYS_STATUS":
            self.voltage = m.voltage_battery
            self.current = m.current_battery
            self.level = m.battery_remaining
            self.__on_change('battery')
        elif typ == "HEARTBEAT":
            self.__on_change('mode', 'armed')
        elif typ in ["WAYPOINT_CURRENT", "MISSION_CURRENT"]:
            self.last_waypoint = m.seq
        elif typ == "RC_CHANNELS_RAW":
            def set(chnum, v):
                '''Private utility for handling rc channel messages'''
                # use port to allow ch nums greater than 8
                self.rc_readback[str(m.port * 8 + chnum)] = v

            set(1, m.chan1_raw)
            set(2, m.chan2_raw)
            set(3, m.chan3_raw)
            set(4, m.chan4_raw)
            set(5, m.chan5_raw)
            set(6, m.chan6_raw)
            set(7, m.chan7_raw)
            set(8, m.chan8_raw)
        elif typ == "MOUNT_STATUS":
            self.mount_pitch = m.pointing_a / 100
            self.mount_roll = m.pointing_b / 100
            self.mount_yaw = m.pointing_c / 100
            self.__on_change('mount')
        elif typ == "RANGEFINDER":
            self.rngfnd_distance = m.distance
            self.rngfnd_voltage = m.voltage
            self.__on_change('rangefinder')

        if self.api:
            for v in self.api.get_vehicles():
                if v.mavrx_callback:
                    v.mavrx_callback(m)

    def prepare(self):
        # print('Await heartbeat.')
        # TODO this should be more rigious. How to avoid
        #   invalid MAVLink prefix '73'
        #   invalid MAVLink prefix '13'
        while True:
            try:
                self.master.wait_heartbeat()
                break
            except mavutil.mavlink.MAVError:
                continue
        # print('DONE')
        
        send_heartbeat(self.master)
        request_data_stream_send(self.master)

        params = type('PState',(object,),{
            "mav_param_count": -1,
            "mav_param_set": []
        })()
        self.mav_param = {}
        self.pstate = params
        self.master.mav.param_request_list_send(self.master.target_system, self.master.target_component)

        def mavlink_thread():
            while True:
                time.sleep(0.1)

                while True:
                    try:
                        msg = self.out_queue.get_nowait()
                        self.master.write(msg)
                    except:
                        break

                while True:
                    try:
                        msg = self.master.recv_msg()
                    except Exception as e:
                        print(e)
                        msg = None
                    if not msg:
                        break

                    if msg.get_type() == 'PARAM_VALUE':
                        if params.mav_param_count == -1:
                            params.mav_param_count = msg.param_count
                            params.mav_param_set = [None]*msg.param_count
                        try:
                            if msg.param_index < msg.param_count:
                                params.mav_param_set[msg.param_index] = msg
                            self.mav_param[msg.param_id] = msg.param_value
                        except:
                            import traceback
                            traceback.print_exc()

                    elif msg.get_type() == 'HEARTBEAT':
                        self.status.armed = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
                        self.status.flightmode = {v: k for k, v in self.master.mode_mapping().items()}[msg.custom_mode]

                    else:
                        if self.api:
                            self.mavlink_packet(msg)

                        # Print unexpected values we don't deal with yet.
                        if msg.get_type() not in swallow:
                            print(msg)

        t = Thread(target=mavlink_thread)
        t.daemon = True
        t.start()

        while True:
            time.sleep(0.1)
            if params.mav_param_count > 0 and None not in params.mav_param_set:
                print('Completed list of %s params' % (params.mav_param_count,))
                print('Starting dronekit.')
                break

        self.api = FakeAPI(self)
        return self.api

import psutil
def kill(proc_pid):
    try:
        process = psutil.Process(proc_pid)
        for proc in process.children(recursive=True):
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
    except psutil.NoSuchProcess:
        pass

def local_connect():
    import droneapi.module.api as api
    state = MPFakeState(mavutil.mavlink_connection('tcp:127.0.0.1:5760'))
    # api.init(state)
    return state.prepare()

import atexit
bg = []
def cleanup_processes():
    for p in bg:
        kill(p.pid)
atexit.register(cleanup_processes)

def sitl_connect():
    import os
    import sys
    from subprocess import Popen, PIPE

    sitl_args = ['dronekit-sitl', 'copter-3.3-rc5', '-I0', '-S', '--model', 'quad', '--home=-35.363261,149.165230,584,353']

    speedup = os.environ.get('TEST_SPEEDUP', '1')
    rate = os.environ.get('TEST_RATE', '200')
    sitl_args += ['--speedup', str(speedup), '-r', str(rate)]

    # Change CPU core affinity.
    # TODO change affinity on osx/linux
    if sys.platform == 'win32':
        # 0x14 = 0b1110 = all cores except cpu 1
        sitl = Popen(['start', '/affinity', '14', '/realtime', '/b', '/wait'] + sitl_args, shell=True, stdout=PIPE, stderr=PIPE)
    else:
        sitl = Popen(sitl_args, stdout=PIPE, stderr=PIPE)
    bg.append(sitl)

    while sitl.poll() == None:
        line = sitl.stdout.readline()
        if 'Waiting for connection' in line:
            break
    if sitl.poll() != None and sitl.returncode != 0:
        print('[runner] ...aborting with SITL error code ' + str(sitl.returncode))
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(sitl.returncode)

    return local_connect()
