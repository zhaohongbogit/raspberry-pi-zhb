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
| SG90 舵机 | 1 | 角度控制 |

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

舵机 (SG90):
  VCC (红) → 5V (Pin 2)
  GND (黑) → GND (Pin 6)
  信号 (橙) → GPIO12 (Pin 32)
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
sudo apt install -y python3-pip python3-venv python3-gpiozero mosquitto mosquitto-clients

# 安装pigpio，启用物理PWM防抖
sudo apt-get update
sudo apt-get install pigpiod
sudo systemctl enable pigpiod
sudo systemctl start pigpiod

# 启用 GPIO
sudo dietpi-config
# 进入 Advanced Options → GPIO → 启用
# RaspberryPi OS Lite
sudo raspi-config
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
pip install RPi.GPIO gpiozero paho-mqtt adafruit-circuitpython-dht pigpio
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

（本 README 中的示例略去部分实现细节，可直接查看 `main_controller.py` 获取最新实现）

### 2. MQTT 控制命令示例

#### （1）单设备控制

- **LED / 风扇 / 水泵 (输出设备)**

```json
{"device":"led1","action":"on"}
{"device":"fan","action":"off"}
```

- **舵机控制（角度）**

```json
{"device":"servo1","action":"angle","value":90}
{"device":"servo2","action":"angle","value":120}
```

- **L298N 电机驱动（motor）**

```json
{"device":"motor","action":"forward"}
{"device":"motor","action":"backward"}
{"device":"motor","action":"stop"}

{"device":"motor","action":"speed","value":0.6}
{"device":"motor","action":"speed","value":-50}  # 支持 -1~1 或 0~100 百分比
```

#### （2）车速 + 方向 一体控制（drive）

一次 MQTT 消息同时控制电机速度和前轮转向：

```json
{
  "device": "drive",
  "action": "set",
  "value": {
    "speed": 0.6,
    "steer": 120
  }
}
```

- `speed`：-1.0（全速反转）~1.0（全速正转），也支持 0~100 百分比
  - 程序默认会自动限速（`MAX_DRIVE_SPEED`）
  - 当转向接近舵机极限时，会自动降低最高速度以减少碰撞风险
- `steer`：0~180（对应舵机角度）
  - 程序会自动限制在安全区间 (`STEER_MIN_ANGLE` / `STEER_MAX_ANGLE`) 以内，避免舵机撞击机械限位

### 主动避障模式（可启/关）

当 `OBSTACLE AVOIDANCE` 开启时，程序会自动根据超声波距离传感器判断是否需要减速/倒退，并尝试让前轮回中保持直行：

- 距离小于 `OBSTACLE_SLOW_DISTANCE`（默认 0.5m）时：自动限速到 `OBSTACLE_SLOW_SPEED`（默认 0.3），并将前轮回中。
- 距离小于 `OBSTACLE_REVERSE_DISTANCE`（默认 0.2m）时：自动倒退（默认速度 -0.4），并尝试返回安全点（回中舵机）。

#### 开关避障模式

```json
{"device":"obstacle","action":"off"}
{"device":"obstacle","action":"on"}
```

#### 返回安全点（停止并回中舵机 + 短暂倒退）

```json
{"device":"return","action":"home"}
```

#### 导航回“起点”或指定坐标（近似）

```json
{"device":"navigate","action":"to","value":{"x":0.0,"y":0.0}}
```

- 该命令会启用简易位置积分（基于时间和速度）来估计当前位置，并向目标点行驶。
- 如果要返回起点，可将坐标设为 `x=0, y=0`。

#### 取消导航

```json
{"device":"navigate","action":"stop"}
```

#### 多点路径导航（依次到达多个点）

```json
{
  "device": "navigate",
  "action": "path",
  "value": [
    {"x": 1.0, "y": 0.0},
    {"x": 1.0, "y": 1.0},
    {"x": 0.0, "y": 1.0}
  ]
}
```

#### 设置家（起点）位置

```json
{"device":"position","action":"set_home","value":{"x":0.5,"y":0.5}}
```

#### 将当前位置设置为家（起点）

```json
{"device":"position","action":"set_current_as_home"}
```

#### 返回到设定的家（起点）

```json
{"device":"navigate","action":"return_home"}
```

#### 设置避障绕行方向（左/右）

```json
{"device":"obstacle","action":"avoid_mode","value":"left"}
```

或

```json
{"device":"obstacle","action":"avoid_mode","value":"right"}
```

#### 传感器接线示例（HC-SR04）

```plain
HC-SR04:
  VCC  → 5V
  GND  → GND
  TRIG → GPIO27
  ECHO → GPIO17
```

> ⚠️ 如果你的接线不同，请修改 `main_controller.py` 中的 `DISTANCE_SENSOR_TRIGGER` / `DISTANCE_SENSOR_ECHO` 常量。

### 进阶功能说明

#### 1. 避障绕行（左/右策略）

当启用了 `obstacle_avoidance` 并设置了 `avoid_mode`（`left` 或 `right`）时，车辆在导航过程中遇到障碍会自动尝试从指定方向绕过障碍，而不是简单倒退。

#### 2. 多点路径规划

使用 `navigate:path` 命令，可以让车辆依次访问多个路径点。系统会自动按顺序到达每个点，适合园林巡回、自动清扫等应用。

#### 3. 自定义起点（Home）管理

车辆支持记录和返回用户定义的"起点"，而不仅仅是坐标原点。这样可以实现：
- 标记工作区起点
- 充电站位置记录
- 随时返回确定已知位置

#### 4. 定位方式（近似里程计）

系统基于 PWM 时间积分 + 舵机角度估算位置。精度有限，适合：
- 短距离导航（< 5m）
- 室内慢速环境
- 初步测试和演示

如需更高精度，建议添加：
- 轮式编码器（增量式）
- 陀螺仪 / IMU（方向校正）
- 主摄像头（视觉里程计）


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

# 控制舵机角度 (0度)
mosquitto_pub -h 树莓派IP -t "rpi3b/control" -m '{"device":"servo1","action":"angle","value":0}'

# 控制舵机角度 (90度)
mosquitto_pub -h 树莓派IP -t "rpi3b/control" -m '{"device":"servo1","action":"angle","value":90}'

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
        
        <div class="device">
            <h3>舵机</h3>
            <input type="range" id="servoAngle" min="-90" max="90" value="0" step="1" oninput="updateServoValue()">
            <span id="servoValue">0</span>°
            <button onclick="controlServo()">设置角度</button>
        </div>
        
        <script>
        function control(device, action) {
            fetch('/control', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({device, action})
            });
        }
        
        function updateServoValue() {
            document.getElementById('servoValue').textContent = document.getElementById('servoAngle').value;
        }
        
        function controlServo() {
            const angle = document.getElementById('servoAngle').value;
            fetch('/control', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({device: 'servo1', action: 'angle', value: parseInt(angle)})
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