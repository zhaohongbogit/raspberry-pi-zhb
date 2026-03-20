#!/usr/bin/env python3
# ~/iot-project/main_controller.py

import json
import math
import time
import board
import adafruit_dht
import paho.mqtt.client as mqtt
from gpiozero import LED, OutputDevice, AngularServo, Motor, DistanceSensor
from gpiozero.pins.pigpio import PiGPIOFactory
from threading import Thread
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化硬件 PWM
factory = PiGPIOFactory()

# MQTT 配置
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC_CONTROL = "rpi3b/control"    # 接收控制命令
MQTT_TOPIC_SENSOR = "rpi3b/sensor"      # 发送传感器数据
MQTT_TOPIC_STATUS = "rpi3b/status"      # 发送设备状态

# GPIO 配置
PINS = {
    'led1': 18,
    'led2': 23,
    'fan': 24,
    'pump': 25,
    'servo1': 12,
    'servo2': 13,  # 前轮转向舵机
}

# L298N 电机驱动 (双向电机控制)
MOTOR_PINS = {
    'forward': 5,
    'backward': 6,
    'enable': 19,
}

# 安全限速 / 转向范围（可根据车体实际调节）
MAX_DRIVE_SPEED = 0.8           # 最高速度限制 (0..1)
STEER_MIN_ANGLE = 20            # 舵机最小角度限制
STEER_MAX_ANGLE = 160           # 舵机最大角度限制
STEER_SLOW_ZONE_DEG = 25        # 当转向接近极限时，限制最大速度
MAX_SPEED_WHEN_TURNING = 0.5    # 转向极限时的最高限速

# 超声波距离传感器（HC-SR04）配置
# 这里为示例 GPIO，按实际接线修改
DISTANCE_SENSOR_ECHO = 17
DISTANCE_SENSOR_TRIGGER = 27
OBSTACLE_SLOW_DISTANCE = 0.50   # 米，距离小于该值时开始减速
OBSTACLE_REVERSE_DISTANCE = 0.20  # 米，距离小于该值时自动倒退
OBSTACLE_SLOW_SPEED = 0.3        # 米/秒等效速度限制（用于减速）
OBSTACLE_REVERSE_SPEED = -0.4    # 倒退速度

# 主动避障 / 返回安全点
SAFE_STEER_ANGLE = 90           # 直行（回中）角度
SAFE_RETURN_REVERSE_TIME = 0.4   # 秒，倒退时长

# 简易导航定位 (基于时间积分，仅用于近似返回)
NAVIGATION_STOP_DISTANCE = 0.15  # m，距离小于该值则停止（目标到达）
NAVIGATION_SPEED = 0.4           # 导航默认速度

# 避障绕行参数
OBSTACLE_AVOID_MODE = None       # None / 'left' / 'right'：绕行方向
OBSTACLE_AVOID_ANGLE_LEFT = 45   # 向左转的舵机角度
OBSTACLE_AVOID_ANGLE_RIGHT = 135 # 向右转的舵机角度
OBSTACLE_AVOID_RETREAT_TIME = 0.3  # 倒退时长（秒）
OBSTACLE_AVOID_TURN_DISTANCE = 0.3  # 转向后继续的距离（m），超过则重新计算

class IoTController:
    def __init__(self):
        self.devices = {}
        self.dht = adafruit_dht.DHT22(board.D4)
        self.running = True
        self.obstacle_avoidance_enabled = True
        self._returning_to_safe = False

        # 简易定位（仅基于时间积分）
        self.position = [0.0, 0.0]  # x, y (m)
        self.heading = 0.0          # 车辆朝向弧度（0=正前方）
        self.home_position = [0.0, 0.0]  # 用户设定的"起点"
        self.navigation_active = False
        self.navigation_target = None
        self.navigation_path = []   # 多点路径队列

        # 避障绕行
        self.obstacle_avoid_mode = None  # None / 'left' / 'right'
        self.obstacle_avoid_start_dist = None  # 避障开始时的距离

        # 初始化设备
        self.setup_devices()

        # 初始化 MQTT
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.on_disconnect = self.on_disconnect

    def setup_devices(self):
        """初始化 GPIO 设备"""
        for name, pin in PINS.items():
            if name.startswith('led'):
                self.devices[name] = LED(pin)
            elif name.startswith('servo'):
                # 采用 pigpio 提供的稳定 PWM
                self.devices[name] = AngularServo(
                    pin,
                    min_angle=0,
                    max_angle=180,
                    min_pulse_width=0.0006,
                    max_pulse_width=0.0024,
                    pin_factory=factory,
                )
            else:
                self.devices[name] = OutputDevice(pin)
            logger.info(f"设备 {name} 初始化在 GPIO{pin}")

        # 初始化 L298N 电机驱动 (双向电机控制)
        self.devices['motor'] = Motor(
            forward=MOTOR_PINS['forward'],
            backward=MOTOR_PINS['backward'],
            enable=MOTOR_PINS['enable'],
            pin_factory=factory,
            pwm=True,
        )
        logger.info(
            f"设备 motor (L298N) 初始化: forward={MOTOR_PINS['forward']} "
            f"backward={MOTOR_PINS['backward']} enable={MOTOR_PINS['enable']}"
        )

        # 初始化超声波距离传感器
        self.devices['distance'] = DistanceSensor(
            echo=DISTANCE_SENSOR_ECHO,
            trigger=DISTANCE_SENSOR_TRIGGER,
            max_distance=2.0,
            threshold_distance=OBSTACLE_SLOW_DISTANCE,
            pin_factory=factory,
        )
        logger.info(
            f"设备 distance (HC-SR04) 初始化: echo={DISTANCE_SENSOR_ECHO} "
            f"trigger={DISTANCE_SENSOR_TRIGGER}"
        )

    def on_connect(self, client, userdata, flags, rc):
        """MQTT 连接回调"""
        if rc == 0:
            logger.info("MQTT 连接成功")
            client.subscribe(MQTT_TOPIC_CONTROL)
            self.publish_status()
        else:
            logger.error(f"MQTT 连接失败，返回码: {rc}")

    def on_disconnect(self, client, userdata, rc):
        """MQTT 断开回调"""
        logger.warning(f"MQTT 断开连接，返回码: {rc}")

    def on_message(self, client, userdata, msg):
        """接收控制命令"""
        try:
            payload = json.loads(msg.payload.decode())
            logger.info("收到命令: {payload}")

            device = payload.get('device')
            action = payload.get('action')
            value = payload.get('value')

            # 支持复合控制命令（例如 drive、obstacle、return、navigate、position）
            if device in self.devices or device in ('drive', 'obstacle', 'return', 'navigate', 'position'):
                self.control_device(device, action, value)
            else:
                logger.warning(f"未知设备: {device}")

        except Exception as e:
            logger.error(f"处理消息失败: {e}")

    def control_device(self, device, action, value=None):
        """控制设备"""
        try:
            # 切换避障模式
            if device == 'obstacle':
                if action in ('on', 'enable', 'true'):
                    self.set_obstacle_avoidance(True)
                elif action in ('off', 'disable', 'false'):
                    self.set_obstacle_avoidance(False)
                elif action == 'avoid_mode':
                    # 设置避障绕行方向
                    if isinstance(value, str) and value in ('left', 'right'):
                        self.obstacle_avoid_mode = value
                        logger.info(f"避障绕行模式: {value}")
                    else:
                        logger.warning("避障绕行需要指定 'left' 或 'right'")
                else:
                    logger.warning(f"未知避障命令: {action}")
                self.publish_status()
                return

            # 返回安全点
            if device == 'return' and action in ('home', 'safe', 'return'):
                self.return_to_safe_point()
                self.publish_status()
                return

            # 导航到指定点（x/y）
            if device == 'navigate':
                if action == 'to':
                    self.navigate_to(value)
                elif action == 'path':
                    # 多点路径导航
                    self.navigate_path(value)
                elif action == 'return_home':
                    # 返回用户设定的起点
                    self.navigate_to({'x': self.home_position[0], 'y': self.home_position[1]})
                elif action in ('stop', 'cancel'):
                    self.navigation_active = False
                    logger.info("导航已取消")
                else:
                    logger.warning(f"未知导航命令: {action}")
                self.publish_status()
                return

            # 位置管理（设置起点等）
            if device == 'position':
                if action == 'set_home':
                    if isinstance(value, dict) and 'x' in value and 'y' in value:
                        self.home_position = [float(value['x']), float(value['y'])]
                        logger.info(f"起点已设置: {self.home_position}")
                    else:
                        logger.warning("set_home 需要包含 x/y 坐标")
                elif action == 'set_current_as_home':
                    self.home_position = self.position.copy()
                    logger.info(f"当前位置已设置为起点: {self.home_position}")
                else:
                    logger.warning(f"未知位置命令: {action}")
                self.publish_status()
                return

            # 复合命令（车速+方向）
            if device == 'drive':
                self._control_drive(value)
                self.publish_status()
                return

            dev = self.devices[device]

            if device.startswith('servo'):
                if action == 'angle' and value is not None:
                    angle = float(value)
                    # 限位保护，防止舵机撞击机械限位
                    if device == 'servo2':
                        if angle < STEER_MIN_ANGLE or angle > STEER_MAX_ANGLE:
                            logger.warning(
                                f"{device} 角度超出安全范围 {STEER_MIN_ANGLE}~{STEER_MAX_ANGLE}，已限制"
                            )
                        angle = max(STEER_MIN_ANGLE, min(STEER_MAX_ANGLE, angle))

                    dev.angle = angle
                    logger.info(f"{device} 设置角度: {angle}")
                else:
                    logger.warning(f"无效的舵机命令: {action}")

            elif device == 'motor':
                # L298N 电机控制: forward/backward/stop/speed
                if action == 'forward':
                    # 默认全速
                    speed = 1.0 if value is None else float(value)
                    dev.forward(speed)
                    logger.info(f"{device} 前进 (速度={speed})")
                elif action == 'backward':
                    speed = 1.0 if value is None else float(value)
                    dev.backward(speed)
                    logger.info(f"{device} 后退 (速度={speed})")
                elif action == 'stop':
                    dev.stop()
                    logger.info(f"{device} 已停止")
                elif action == 'speed' and value is not None:
                    # 支持 -1.0 至 1.0，或 0-100 的百分比
                    speed = float(value)
                    if abs(speed) > 1 and abs(speed) <= 100:
                        speed = speed / 100.0
                    dev.value = max(-1.0, min(1.0, speed))
                    logger.info(f"{device} 速度设置: {dev.value}")
                else:
                    logger.warning(f"无效的电机命令: {action}")

            else:
                if action == 'on':
                    dev.on()
                    logger.info(f"{device} 已开启")
                elif action == 'off':
                    dev.off()
                    logger.info(f"{device} 已关闭")
                elif action == 'toggle':
                    dev.toggle()
                    logger.info(f"{device} 已切换状态")

            self.publish_status()

        except Exception as e:
            logger.error(f"控制设备失败: {e}")

    def _control_drive(self, value):
        """复合控制命令：同时设置车速（motor）和转向（servo2）"""
        if not isinstance(value, dict):
            logger.warning("drive 命令需要包含 speed/steer 字段")
            return

        # 手动 drive 命令会中断导航
        self.navigation_active = False

        speed = value.get('speed')
        steer = value.get('steer')

        if speed is not None:
            # 复用 motor speed 设置逻辑
            motor = self.devices.get('motor')
            try:
                s = float(speed)
                if abs(s) > 1 and abs(s) <= 100:
                    s = s / 100.0

                # 限速保护
                if s > 0:
                    s = min(s, MAX_DRIVE_SPEED)
                else:
                    s = max(s, -MAX_DRIVE_SPEED)

                # 当转向接近极限时降低最大速度，减少碰撞风险
                if steer is not None:
                    try:
                        steer_val = float(steer)
                        if (steer_val <= STEER_MIN_ANGLE + STEER_SLOW_ZONE_DEG
                                or steer_val >= STEER_MAX_ANGLE - STEER_SLOW_ZONE_DEG):
                            s = max(-MAX_SPEED_WHEN_TURNING, min(MAX_SPEED_WHEN_TURNING, s))
                    except Exception:
                        pass

                # 如果避障模式打开，进行距离检测并自动减速/倒退
                if self.obstacle_avoidance_enabled:
                    distance = None
                    distance_sensor = self.devices.get('distance')
                    if isinstance(distance_sensor, DistanceSensor):
                        try:
                            distance = distance_sensor.distance
                        except Exception:
                            distance = None

                    if distance is not None:
                        # 近距离时减速
                        if distance < OBSTACLE_SLOW_DISTANCE:
                            s = max(-OBSTACLE_SLOW_SPEED, min(OBSTACLE_SLOW_SPEED, s))
                            logger.info(f"drive: 距离 {distance:.2f}m，已限速 {s}")
                            # 主动转向避障：回中舵机以保持直线
                            servo = self.devices.get('servo2')
                            if servo:
                                servo.angle = SAFE_STEER_ANGLE

                        # 非常近时自动倒退
                        if distance < OBSTACLE_REVERSE_DISTANCE:
                            s = OBSTACLE_REVERSE_SPEED
                            logger.warning(
                                f"drive: 距离 {distance:.2f}m，触发倒退 (speed={s})"
                            )
                            # 同时让车辆回到安全点
                            self.return_to_safe_point()

                motor.value = max(-1.0, min(1.0, s))
                logger.info(f"drive: 设置速度 {motor.value}")
            except Exception as e:
                logger.warning(f"drive speed 设置失败: {e}")

        if steer is not None:
            servo = self.devices.get('servo2')
            try:
                angle = float(steer)
                if angle < STEER_MIN_ANGLE or angle > STEER_MAX_ANGLE:
                    logger.warning(
                        f"drive steer 超出安全范围 {STEER_MIN_ANGLE}~{STEER_MAX_ANGLE}，已限制"
                    )
                # 限位保护
                angle = max(STEER_MIN_ANGLE, min(STEER_MAX_ANGLE, angle))
                servo.angle = angle
                logger.info(f"drive: 设置转向角度 {angle}")
            except Exception as e:
                logger.warning(f"drive steer 设置失败: {e}")

    def set_obstacle_avoidance(self, enabled: bool):
        """开关避障模式"""
        self.obstacle_avoidance_enabled = bool(enabled)
        logger.info(f"避障模式 {'开启' if self.obstacle_avoidance_enabled else '关闭'}")

    def return_to_safe_point(self):
        """将车辆返回安全点（停止并回中舵机）"""
        # 通过导航到原点的方式返回（可被中断）
        self.navigate_to({'x': 0.0, 'y': 0.0})

    def navigate_to(self, target, speed=None):
        """导航到目标坐标（近似，基于时间积分）。支持避障绕行。"""
        # 目标坐标格式：{'x': float, 'y': float}
        if not isinstance(target, dict) or 'x' not in target or 'y' not in target:
            logger.warning("navigate 命令需要包含 x/y 坐标")
            return

        if self.navigation_active:
            # 取消已有导航
            self.navigation_active = False
            time.sleep(0.1)

        self.navigation_target = (float(target['x']), float(target['y']))
        self.navigation_active = True
        self.obstacle_avoid_start_dist = None

        def _nav_loop():
            motor = self.devices.get('motor')
            servo = self.devices.get('servo2')
            distance_sensor = self.devices.get('distance')
            speed_value = NAVIGATION_SPEED if speed is None else float(speed)

            while self.running and self.navigation_active:
                # 计算当前误差
                dx = self.navigation_target[0] - self.position[0]
                dy = self.navigation_target[1] - self.position[1]
                dist = math.hypot(dx, dy)

                if dist <= NAVIGATION_STOP_DISTANCE:
                    logger.info(f"已到达导航目标: {self.navigation_target}")
                    break

                # 检查超声波距离（避障逻辑）
                current_distance = None
                if isinstance(distance_sensor, DistanceSensor) and self.obstacle_avoidance_enabled:
                    try:
                        current_distance = distance_sensor.distance
                    except Exception:
                        pass

                # 处理避障绕行
                if current_distance is not None and current_distance < OBSTACLE_SLOW_DISTANCE:
                    if self.obstacle_avoid_mode:
                        # 执行避障绕行
                        avoid_angle = (OBSTACLE_AVOID_ANGLE_LEFT if self.obstacle_avoid_mode == 'left'
                                       else OBSTACLE_AVOID_ANGLE_RIGHT)
                        if servo:
                            servo.angle = avoid_angle
                        if motor:
                            motor.value = NAVIGATION_SPEED * 0.5
                        logger.info(f"避障绕行 ({self.obstacle_avoid_mode}): 角度 {avoid_angle}")
                        time.sleep(0.2)
                    else:
                        # 未设置绕行模式，就倒退
                        if motor:
                            motor.value = -0.3
                        time.sleep(OBSTACLE_AVOID_RETREAT_TIME)
                        if motor:
                            motor.stop()
                        self.obstacle_avoid_start_dist = None
                else:
                    # 正常导航
                    target_heading = math.atan2(dy, dx)
                    target_angle = 90 + math.degrees(target_heading)
                    target_angle = max(STEER_MIN_ANGLE, min(STEER_MAX_ANGLE, target_angle))

                    if servo:
                        servo.angle = target_angle

                    v = max(-1.0, min(1.0, speed_value))
                    if motor:
                        motor.value = v

                time.sleep(0.1)

            # 停止
            if motor:
                motor.stop()
            self.navigation_active = False

        Thread(target=_nav_loop, daemon=True).start()

    def navigate_path(self, waypoints):
        """批量导航：依次到达多个路径点。"""
        if not isinstance(waypoints, list):
            logger.warning("navigate_path 需要包含路径点列表")
            return

        # 清空路径队列，添加新路径
        self.navigation_path = [{'x': float(wp.get('x', 0)), 'y': float(wp.get('y', 0))}
                                for wp in waypoints if isinstance(wp, dict)]

        if not self.navigation_path:
            logger.warning("路径点列表为空")
            return

        self.navigation_active = True

        def _path_loop():
            for i, waypoint in enumerate(self.navigation_path):
                if not self.navigation_active:
                    logger.info("路径导航已取消")
                    break

                logger.info(f"前往路径点 {i+1}/{len(self.navigation_path)}: {waypoint}")
                self.navigate_to(waypoint)

                # 等待到达该路径点
                while self.navigation_active and self.running:
                    if self.navigation_target is None:
                        break
                    time.sleep(0.1)

            logger.info("路径导航完成")
            self.navigation_active = False

        Thread(target=_path_loop, daemon=True).start()

    def _update_odometry(self, dt: float):
        """基于当前速度和舵机角度，更新位置和朝向（近似）。"""
        motor = self.devices.get('motor')
        servo = self.devices.get('servo2')
        if motor is None or servo is None:
            return

        # 速度估算：将 motor.value 转换为米/秒
        speed = (motor.value or 0.0) * MAX_DRIVE_SPEED
        # 舵机 90 度为正前方，偏移转换为弧度
        heading = math.radians(servo.angle - 90)

        # 记录当前朝向
        self.heading = heading

        # 简易位置积分
        dx = math.cos(heading) * speed * dt
        dy = math.sin(heading) * speed * dt
        self.position[0] += dx
        self.position[1] += dy

    def _odometry_loop(self):
        """定时更新车辆位置（用于导航）。"""
        last = time.time()
        while self.running:
            now = time.time()
            dt = now - last
            last = now
            self._update_odometry(dt)
            time.sleep(0.1)

    def publish_status(self):
        """发布设备状态"""
        status = {
            'timestamp': time.time(),
            'modes': {
                'obstacle_avoidance': self.obstacle_avoidance_enabled,
                'obstacle_avoid_mode': self.obstacle_avoid_mode,
                'returning_to_safe': self._returning_to_safe,
                'navigation_active': self.navigation_active,
            },
            'pose': {
                'x': round(self.position[0], 2),
                'y': round(self.position[1], 2),
                'heading': round(math.degrees(self.heading), 1),
            },
            'home': {
                'x': round(self.home_position[0], 2),
                'y': round(self.home_position[1], 2),
            },
            'devices': {}
        }
        for name, dev in self.devices.items():
            if isinstance(dev, AngularServo):
                status['devices'][name] = dev.angle
            elif isinstance(dev, Motor):
                # Motor.value 代表速度和方向，范围 -1..1
                status['devices'][name] = round(dev.value or 0.0, 2)
            elif isinstance(dev, DistanceSensor):
                # 距离传感器返回米
                status['devices'][name] = round(dev.distance, 2)
            else:
                status['devices'][name] = 'on' if dev.value else 'off'
        self.mqtt_client.publish(MQTT_TOPIC_STATUS, json.dumps(status))

    def read_sensor(self):
        """读取传感器数据"""
        try:
            temperature = self.dht.temperature
            humidity = self.dht.humidity

            if temperature is not None and humidity is not None:
                data = {
                    'timestamp': time.time(),
                    'temperature': round(temperature, 1),
                    'humidity': round(humidity, 1)
                }
                self.mqtt_client.publish(MQTT_TOPIC_SENSOR, json.dumps(data))
                logger.info(f"传感器数据: {data}")

        except Exception as e:
            logger.error(f"读取传感器失败: {e}")

    def sensor_loop(self):
        """传感器读取循环"""
        while self.running:
            self.read_sensor()
            time.sleep(10)  # 每 10 秒读取一次

    def run(self):
        """主运行循环"""
        try:
            # 连接 MQTT
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

            # 启动传感器线程（后台跑，避免阻塞 MQTT 循环）
            # sensor_thread = Thread(target=self.sensor_loop)
            # sensor_thread.daemon = True
            # sensor_thread.start()

            # 启动里程计线程（用于导航定位）
            # odom_thread = Thread(target=self._odometry_loop)
            # odom_thread.daemon = True
            # odom_thread.start()

            # MQTT 网络循环
            self.mqtt_client.loop_forever()

        except KeyboardInterrupt:
            logger.info("程序终止")
        finally:
            self.cleanup()

    def cleanup(self):
        """清理资源"""
        self.running = False
        try:
            self.mqtt_client.disconnect()
        except Exception:
            pass

        for dev in self.devices.values():
            # 先尝试优雅关闭（stop/close/off），再 detach
            if hasattr(dev, 'stop'):
                try:
                    dev.stop()
                except Exception:
                    pass
            if hasattr(dev, 'close'):
                try:
                    dev.close()
                except Exception:
                    pass
            elif hasattr(dev, 'off'):
                try:
                    dev.off()
                except Exception:
                    pass
            elif hasattr(dev, 'detach'):
                try:
                    dev.detach()
                except Exception:
                    pass

        logger.info("资源已清理")

if __name__ == '__main__':
    controller = IoTController()
    controller.run()