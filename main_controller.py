#!/usr/bin/env python3
# ~/iot-project/main_controller.py

import json
import time
import board
import adafruit_dht
import paho.mqtt.client as mqtt
from gpiozero import LED, OutputDevice
from threading import Thread
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
    'pump': 25
}

class IoTController:
    def __init__(self):
        self.devices = {}
        self.dht = adafruit_dht.DHT22(board.D4)
        self.running = True
        
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
            else:
                self.devices[name] = OutputDevice(pin)
            logger.info(f"设备 {name} 初始化在 GPIO{pin}")
    
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
            logger.info(f"收到命令: {payload}")
            
            device = payload.get('device')
            action = payload.get('action')
            
            if device in self.devices:
                self.control_device(device, action)
            else:
                logger.warning(f"未知设备: {device}")
                
        except Exception as e:
            logger.error(f"处理消息失败: {e}")
    
    def control_device(self, device, action):
        """控制设备"""
        try:
            dev = self.devices[device]
            
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
    
    def publish_status(self):
        """发布设备状态"""
        status = {
            'timestamp': time.time(),
            'devices': {
                name: 'on' if dev.value else 'off' 
                for name, dev in self.devices.items()
            }
        }
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
            
            # 启动传感器线程
            # sensor_thread = Thread(target=self.sensor_loop)
            # sensor_thread.daemon = True
            # sensor_thread.start()
            
            # MQTT 网络循环
            self.mqtt_client.loop_forever()
            
        except KeyboardInterrupt:
            logger.info("程序终止")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """清理资源"""
        self.running = False
        self.mqtt_client.disconnect()
        for dev in self.devices.values():
            dev.off()
        logger.info("资源已清理")

if __name__ == '__main__':
    controller = IoTController()
    controller.run()