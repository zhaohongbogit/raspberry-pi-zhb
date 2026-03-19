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