import json
import time
import paho.mqtt.client as mqtt

broker_host = "127.0.0.1"
broker_port = 2552

bot_id = "specialized_lp_bot_20260516_182559"
topic = f"hbot/{bot_id}/start"

message = {
    "header": {
        "timestamp": int(time.time() * 1000),
        "reply_to": f"hummingbot-api-response-{int(time.time() * 1000)}",
        "msg_id": int(time.time() * 1000),
        "node_id": "hummingbot-api",
        "agent": "hummingbot-api",
        "properties": {},
    },
    "data": {},
}

client = mqtt.Client()
client.connect(broker_host, broker_port, 60)
client.publish(topic, json.dumps(message))
client.disconnect()
print("Message sent to " + topic)
