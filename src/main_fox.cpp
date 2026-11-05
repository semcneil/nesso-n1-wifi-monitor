/*
  This script demonstrates MQTT connection over secure websockets (WSS)

  Seth McNeill
  2026 August 17
*/

/*
  main_fox.cpp — Wi-Fi Tracking Beacon & MQTT Telemetry Transmitter
  Arduino Nesso N1 (ESP32-C6)
*/

#include <Arduino.h>
#include <Arduino_Nesso_N1.h>
#include <WiFi.h>
#include "mqtt_client.h" // Native Espressif MQTT framework
#include "time.h"
#include <esp_crt_bundle.h>
#include <esp_mac.h>
#include <ArduinoJson.h>

#include "secrets.h"

// --- CONFIGURATION CONSTANTS ---
const unsigned long MQTT_INTERVAL_MS = 5 * 60 * 1000; // MQTT Publish Interval (5 minutes)
const unsigned long WIFI_BEACON_INTERVAL_MS = 2000;    // Wi-Fi transmission burst interval (2 seconds)

// --- CONFIGURATION FROM SECRETS ---
const char* ssid     = SSID;
const char* password = WIFI_PWD;

const char* mqtt_host  = MQTT_SERVER;
const int   mqtt_port  = 443;
const char* mqtt_path  = MQTT_PATH;
const char* mqtt_topic = MQTT_TOPIC;

const char* mqtt_user = MQTT_USER;
const char* mqtt_pass = MQTT_PWD;

String modName = MOD_NAME;
String globalDeviceId = "ID NOT SET";
String wifiMacStr = "";

// --- NESSO N1 HARDWARE OBJECTS & SPRITES ---
NessoBattery battery;
NessoDisplay display;
LGFX_Sprite statusSprite(&display);

// LED Indicator Position (Matching Radar layout)
const int LED_X = 220;
const int LED_Y = 115;
const int LED_R = 9;

// --- GLOBAL STATE ---
esp_mqtt_client_handle_t client;
bool mqttConnected = false;

unsigned long lastMqttPublishTime = 0;
unsigned long lastWifiBeaconTime = 0;
unsigned long lastTxBlinkTime = 0;

bool isTxBlinking = false;
bool lastChargingState = false;
unsigned long lastBatteryCheck = 0;

// Draw the dedicated RX/TX LED indicator on screen
void draw_tx_led(bool is_red) {
    uint16_t color = is_red ? TFT_RED : TFT_GREEN;
    display.fillCircle(LED_X, LED_Y, LED_R, color);
    display.drawCircle(LED_X, LED_Y, LED_R + 1, TFT_WHITE);
}

// Render base UI screen matching the Nesso N1 Wi-Fi Radar layout
void render_full_ui() {
    statusSprite.fillScreen(TFT_BLACK);

    // 1. Battery Telemetry
    int bat_pct = battery.getChargeLevel();
    lastChargingState = (battery.getChargeStatus() == NessoBattery::CHARGING);

    // 2. Title & Module Name
    statusSprite.setCursor(5, 5);
    statusSprite.setTextColor(TFT_WHITE);
    statusSprite.setTextSize(2);
    statusSprite.print(modName);

    // 3. Top-Right Battery Contour Widget
    uint16_t bat_color = (bat_pct > 20) ? TFT_GREEN : TFT_RED;
    if (lastChargingState) bat_color = TFT_YELLOW;

    int bat_x = 190;
    int bat_y = 5;
    statusSprite.drawRect(bat_x, bat_y, 28, 14, TFT_WHITE);
    statusSprite.fillRect(bat_x + 28, bat_y + 4, 3, 6, TFT_WHITE);

    int fill_width = map(constrain(bat_pct, 0, 100), 0, 100, 0, 24);
    if (fill_width > 0) {
        statusSprite.fillRect(bat_x + 2, bat_y + 2, fill_width, 10, bat_color);
    }

    statusSprite.setCursor(bat_x - 35, bat_y + 3);
    statusSprite.setTextSize(1);
    statusSprite.setTextColor(bat_color);
    if (lastChargingState) {
        statusSprite.print("CHG");
    } else {
        statusSprite.printf("%d%%", bat_pct);
    }

    // 4. MQTT Connection Status Badge
    if (mqttConnected) {
        statusSprite.fillRect(5, 28, 85, 18, TFT_GREEN);
        statusSprite.setCursor(10, 33);
        statusSprite.setTextColor(TFT_BLACK);
        statusSprite.setTextSize(1);
        statusSprite.print("MQTT: ONLINE");
    } else {
        statusSprite.fillRect(5, 28, 85, 18, TFT_RED);
        statusSprite.setCursor(10, 33);
        statusSprite.setTextColor(TFT_WHITE);
        statusSprite.setTextSize(1);
        statusSprite.print("MQTT: OFFLINE");
    }

    // 5. Wi-Fi MAC Address Display
    statusSprite.setCursor(5, 52);
    statusSprite.setTextColor(TFT_YELLOW);
    statusSprite.setTextSize(1);
    statusSprite.print("Wi-Fi MAC:");

    statusSprite.setCursor(5, 64);
    statusSprite.setTextColor(TFT_WHITE);
    statusSprite.setTextSize(2);
    statusSprite.print(wifiMacStr);

    // 6. Battery Voltage Display
    statusSprite.setCursor(5, 90);
    statusSprite.setTextColor(TFT_CYAN);
    statusSprite.setTextSize(2);
    statusSprite.printf("Batt: %.2fV (%d%%)", battery.getVoltage(), bat_pct);

    // 7. Beacon Status Label
    statusSprite.setCursor(5, 112);
    statusSprite.setTextColor(TFT_MAGENTA);
    statusSprite.setTextSize(1);
    statusSprite.print("TX Beacon Active");

    // Push full buffer to display
    statusSprite.pushSprite(0, 0);

    // Draw LED indicator on top
    draw_tx_led(isTxBlinking);
}

// Flash on-screen LED red during active transmissions
void trigger_tx_blink() {
    isTxBlinking = true;
    lastTxBlinkTime = millis();
    draw_tx_led(true);
}

// MQTT Native Event Handler
static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    auto event = (esp_mqtt_event_handle_t)event_data;
    switch ((esp_mqtt_event_id_t)event_id) {
        case MQTT_EVENT_CONNECTED:
            Serial.println("[SUCCESS] Native MQTT Connected over WSS!");
            mqttConnected = true;
            esp_mqtt_client_subscribe(client, mqtt_topic, 1);
            render_full_ui();
            break;
            
        case MQTT_EVENT_DISCONNECTED:
            Serial.println("[ERROR] MQTT Disconnected.");
            mqttConnected = false;
            render_full_ui();
            break;
            
        case MQTT_EVENT_DATA:
            if (String(event->data).indexOf("Beep") >= 0) {
                tone(BEEP_PIN, 750, 500);
            }
            break;
            
        default:
            break;
    }
}

String getUniqueIdString() {
    uint8_t mac8[8];
    char buffer[24];
    if (esp_read_mac(mac8, ESP_MAC_IEEE802154) == ESP_OK) {
        snprintf(buffer, sizeof(buffer), "%02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X",
                 mac8[0], mac8[1], mac8[2], mac8[3], 
                 mac8[4], mac8[5], mac8[6], mac8[7]);
        return String(buffer);
    }
    return String("ERROR_READING_ID");
}

// Publish MQTT Status Telemetry
void publish_mqtt_telemetry() {
    if (!mqttConnected) return;

    trigger_tx_blink();

    JsonDocument doc;
    doc["modName"] = modName;
    doc["wifiMAC"] = wifiMacStr;
    doc["batteryVoltage"] = battery.getVoltage();
    doc["batteryChargePct"] = battery.getChargeLevel();
    doc["globalDeviceId"] = globalDeviceId;
    doc["isCharging"] = (battery.getChargeStatus() == NessoBattery::CHARGING);
    doc["millis"] = millis();

    String jsonBuffer;
    serializeJson(doc, jsonBuffer);

    Serial.printf("[STATUS] Publishing MQTT Telemetry: '%s'\n", jsonBuffer.c_str());
    esp_mqtt_client_publish(client, mqtt_topic, jsonBuffer.c_str(), 0, 1, 0);

    lastMqttPublishTime = millis();
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 2000);

    // Initialize Hardware & Display
    battery.begin();
    battery.enableCharge();
    display.begin();
    display.setRotation(1);

    statusSprite.createSprite(display.width(), display.height());
    pinMode(KEY1, INPUT_PULLUP);

    globalDeviceId = getUniqueIdString();

    // 1. Connect Wi-Fi
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);
    Serial.printf("[STATUS] Connecting to Wi-Fi: %s\n", ssid);
    while (WiFi.status() != WL_CONNECTED) {
        delay(250);
        Serial.print(".");
    }
    Serial.println("\n[SUCCESS] Wi-Fi Connected!");
    wifiMacStr = WiFi.macAddress();

    render_full_ui();

    // 2. Sync System Clock
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");

    // 3. Configure Native WSS MQTT Client
    esp_mqtt_client_config_t mqtt_cfg = {};
    mqtt_cfg.broker.address.hostname = mqtt_host;
    mqtt_cfg.broker.address.transport = MQTT_TRANSPORT_OVER_WSS;
    mqtt_cfg.broker.address.port = mqtt_port;
    mqtt_cfg.broker.address.path = mqtt_path;

    mqtt_cfg.credentials.username = mqtt_user;
    mqtt_cfg.credentials.authentication.password = mqtt_pass;
    
    char client_id[40];
    snprintf(client_id, sizeof(client_id), "esp32_c6_%s", globalDeviceId.c_str());
    mqtt_cfg.credentials.client_id = client_id;
    mqtt_cfg.broker.verification.crt_bundle_attach = esp_crt_bundle_attach;

    client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(client, (esp_mqtt_event_id_t)ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(client);
}

void loop() {
    unsigned long currentMillis = millis();

    // 1. Transmit periodic Wi-Fi beacon bursts every 2 seconds for sniffer tracking
    if (currentMillis - lastWifiBeaconTime >= WIFI_BEACON_INTERVAL_MS) {
        lastWifiBeaconTime = currentMillis;
        trigger_tx_blink();

        // Non-blocking quick scan forces active probe requests/frames over the air
        WiFi.scanNetworks(true, false);
    }

    // 2. Transmit MQTT Telemetry every 5 minutes (or on initial boot connection)
    if (mqttConnected && ((currentMillis - lastMqttPublishTime >= MQTT_INTERVAL_MS) || lastMqttPublishTime == 0)) {
        publish_mqtt_telemetry();
    }

    // 3. Turn off TX LED indicator after 120ms of flash activity
    if (isTxBlinking && (currentMillis - lastTxBlinkTime > 120)) {
        isTxBlinking = false;
        draw_tx_led(false);
    }

    // 4. Periodically refresh battery status and charging state
    if (currentMillis - lastBatteryCheck > 2000) {
        lastBatteryCheck = currentMillis;
        bool is_chg = (battery.getChargeStatus() == NessoBattery::CHARGING);
        if (is_chg != lastChargingState) {
            render_full_ui();
        }
    }

    // Yield execution to prevent CPU starvation
    delay(10);
}