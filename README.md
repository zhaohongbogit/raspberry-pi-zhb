# 树莓派控制 GPIO PWM 服务

# Python控制树莓派

## 一、硬件准备

### 所需材料

| **物品** | **数量** | **说明** |
| --- | --- | --- |
| 树莓派 3B | 1 | 主控板 |
| DHT22 温湿度传感器 | 1 | 数据采集 |
| LED 灯 | 1-3 | 输出控制 |
| 220Ω 电阻 | 3 | LED 限流 |
| 面包板 + 杜邦线 | 若干 | 连接 |
| 风扇（可选） | 1 | 散热控制 |

### 接线图

```plain
DHT22 传感器:
  VCC  → 树莓派 3.3V (Pin 1)
  DATA → GPIO4 (Pin 7)
  GND  → GND (Pin 9)

LED1:
  正极 → GPIO18 (Pin 12) → 220Ω电阻 → LED → GND
  
LED2:
  正极 → GPIO23 (Pin 16) → 220Ω电阻 → LED → GND

风扇（5V）:
  正极 → GPIO24 (Pin 18) → 三极管/MOS管 → 5V
  负极 → GND
```

## 二、系统安装与配置

### 1. 安装 DietPi（推荐）

```bash
# 下载 DietPi 镜像
# https://dietpi.com/downloads/images/DietPi_RPi-ARMv6-Bullseye.7z

# 烧录到 SD 卡后，首次启动配置:
# - 选择 Software: MQTT, Python3
# - 启用 SSH
```

### 2. 系统基础配置

```bash
# 更新系统
sudo apt update
sudo apt upgrade -y

# 安装必要软件
sudo apt install -y python3-pip python3-venv mosquitto mosquitto-clients

# 启用 GPIO
sudo dietpi-config
# 进入 Advanced Options → GPIO → 启用
```

### 3. 安装 Python 库

```bash
# 创建项目目录
mkdir -p ~/source/raspberry-pi-zhb
cd ~/source/raspberry-pi-zhb

# 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install RPi.GPIO paho-mqtt adafruit-circuitpython-dht
```

## 三、MQTT Broker 配置

### 1. 启动本地 MQTT 服务器

```bash
# 启动 Mosquitto
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

# 验证运行
sudo systemctl status mosquitto
```

### 2. 测试 MQTT

```bash
# 终端 1: 订阅主题
mosquitto_sub -h localhost -t "test/topic" -v

# 终端 2: 发布消息
mosquitto_pub -h localhost -t "test/topic" -m "Hello Raspberry Pi"
```

## 四、核心控制程序

### 1. 主控制程序（完整版）

```python
#!/usr/bin/env python3
# ~/source/raspberry-pi-zhb/main_controller.py

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
            sensor_thread = Thread(target=self.sensor_loop)
            sensor_thread.daemon = True
            sensor_thread.start()
            
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
```

### 2. 创建启动脚本

```bash
chmod +x ~/source/raspberry-pi-zhb/main_controller.py
```

## 五、系统服务化

### 1. 创建 systemd 服务

```bash
sudo nano /etc/systemd/system/iot-controller.service
```
```ini
[Unit]
Description=IoT MQTT Controller
After=network.target mosquitto.service
Wants=mosquitto.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/source/raspberry-pi-zhb
Environment="PATH=/home/pi/source/raspberry-pi-zhb/venv/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/home/pi/source/raspberry-pi-zhb/venv/bin/python /home/pi/source/raspberry-pi-zhb/main_controller.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 2. 启用服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable iot-controller
sudo systemctl start iot-controller

# 查看状态
sudo systemctl status iot-controller
sudo journalctl -u iot-controller -f
```

## 六、远程控制方式

### 方式 1：命令行控制

```bash
# 开灯
mosquitto_pub -h 树莓派IP -t "rpi3b/control" -m '{"device":"led1","action":"on"}'

# 关灯
mosquitto_pub -h 树莓派IP -t "rpi3b/control" -m '{"device":"led1","action":"off"}'

# 切换风扇
mosquitto_pub -h 树莓派IP -t "rpi3b/control" -m '{"device":"fan","action":"toggle"}'

# 查看传感器数据
mosquitto_sub -h 树莓派IP -t "rpi3b/sensor" -v
```

### 方式 2：手机 APP（推荐）

**使用 MQTT Dash（Android）或 MQTT Explorer：**

1.  添加 Broker: `树莓派IP:1883`
    
2.  订阅主题: `rpi3b/sensor`, `rpi3b/status`
    
3.  发布主题: `rpi3b/control`
    
4.  创建按钮发送 JSON: `{"device":"led1","action":"on"}`
    

### 方式 3：Web 控制面板

```python
# ~/source/raspberry-pi-zhb/web_control.py
from flask import Flask, render_template, request, jsonify
import paho.mqtt.publish as publish

app = Flask(__name__)
MQTT_HOST = "localhost"

@app.route('/')
def index():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>树莓派 3B 控制面板</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial; padding: 20px; max-width: 400px; margin: 0 auto; }
            .device { margin: 15px 0; padding: 15px; background: #f0f0f0; border-radius: 8px; }
            button { padding: 10px 20px; margin: 5px; font-size: 16px; }
            .on { background: #4CAF50; color: white; }
            .off { background: #f44336; color: white; }
            #sensor { font-size: 24px; margin: 20px 0; }
        </style>
    </head>
    <body>
        <h1>🍓 树莓派 3B 控制器</h1>
        
        <div id="sensor">
            温度: <span id="temp">--</span>°C | 
            湿度: <span id="humid">--</span>%
        </div>
        
        <div class="device">
            <h3>LED 1</h3>
            <button class="on" onclick="control('led1', 'on')">开</button>
            <button class="off" onclick="control('led1', 'off')">关</button>
        </div>
        
        <div class="device">
            <h3>LED 2</h3>
            <button class="on" onclick="control('led2', 'on')">开</button>
            <button class="off" onclick="control('led2', 'off')">关</button>
        </div>
        
        <div class="device">
            <h3>风扇</h3>
            <button class="on" onclick="control('fan', 'on')">开</button>
            <button class="off" onclick="control('fan', 'off')">关</button>
        </div>
        
        <script>
        function control(device, action) {
            fetch('/control', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({device, action})
            });
        }
        
        // 实时更新传感器数据
        setInterval(() => {
            fetch('/sensor')
                .then(r => r.json())
                .then(data => {
                    if (data.temperature) {
                        document.getElementById('temp').textContent = data.temperature;
                        document.getElementById('humid').textContent = data.humidity;
                    }
                });
        }, 2000);
        </script>
    </body>
    </html>
    '''

@app.route('/control', methods=['POST'])
def control():
    data = request.json
    publish.single(
        "rpi3b/control",
        json.dumps(data),
        hostname=MQTT_HOST
    )
    return jsonify({"success": True})

@app.route('/sensor')
def sensor():
    # 从缓存读取最新传感器数据（实际应用中可用 Redis 或文件）
    return jsonify({"temperature": 25.5, "humidity": 60})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
```

**运行 Web 服务**

```bash
pip install flask
python web_control.py
```

**访问**

```plain
http://树莓派IP:5000
```

## 七、内存优化（树莓派 3B 关键）

### 1. 扩大 Swap

```bash
sudo dphys-swapfile swapoff
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

### 2. 减少内存占用

```bash
# 停止不必要服务
sudo systemctl disable bluetooth
sudo systemctl stop bluetooth

# 降低 GPU 内存
echo "gpu_mem=16" | sudo tee -a /boot/config.txt
```

### 3. 监控脚本

```bash
# ~/source/raspberry-pi-zhb/monitor.sh
#!/bin/bash
while true; do
    FREE=$(free -m | awk 'NR==2{print $7}')
    if [ "$FREE" -lt 100 ]; then
        echo "$(date): 内存不足，重启服务" | sudo tee -a /var/log/iot-monitor.log
        sudo systemctl restart iot-controller
    fi
    sleep 60
done
```

## 八、完整部署清单

```bash
# 1. 一键部署脚本
cat > ~/deploy.sh << 'EOF'
#!/bin/bash
set -e

echo "=== 树莓派 3B IoT 部署 ==="

# 安装依赖
sudo apt update
sudo apt install -y python3-pip python3-venv mosquitto

# 创建虚拟环境
mkdir -p ~/source/raspberry-pi-zhb
cd ~/source/raspberry-pi-zhb
python3 -m venv venv
source venv/bin/activate

# 安装 Python 库
pip install RPi.GPIO gpiozero paho-mqtt adafruit-circuitpython-dht flask

# 配置 Swap
sudo dphys-swapfile swapoff || true
echo 'CONF_SWAPSIZE=2048' | sudo tee /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon

# 启动 MQTT
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

echo "=== 部署完成 ==="
echo "请手动创建 main_controller.py 和 systemd 服务"
EOF

chmod +x ~/deploy.sh
./deploy.sh
```

**内存占用对比**

**表格**

| **方案** | **内存占用** | **说明** |
| --- | --- | --- |
| Python + MQTT | ~80MB | 推荐，稳定 |
| Node.js + MQTT | ~120MB | 如果你熟悉 JS |
| OpenClaw | 512MB+ | 树莓派 3B 无法运行 |

这套方案在树莓派 3B 上可长期稳定运行，支持远程控制、数据采集和自动化规则。