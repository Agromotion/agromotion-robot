#include <ArduinoJson.h>
#include <TinyGPS++.h>
#include <Arduino_LPS22HB.h> 

// --- Configurações de Hardware ---
const float BATT_MAX = 14.0;
const float BATT_MIN = 10.0;
const float DIVISOR_RATIO = 5.0;
const float ADC_REF = 3.3;
const float CURRENT_ZERO_V = 2.5; 
const float CURRENT_SENS = 0.100;

struct MotorPins {
  int rpwm, lpwm, ren, len;
};

// Pinos funcionais do segundo script (IBT-2 / BTS7960)
MotorPins mLeft = {4, 5, 6, 7}; 
MotorPins mRight = {8, 9, 10, 11};

const int battery_voltage_pin = A0;
const int current_sensor_pin = A1;

// --- Variáveis de Estado ---
TinyGPSPlus gps;
unsigned long command_timeout = 0;
unsigned long last_gps_send = 0;
unsigned long last_battery_send = 0;
float smoothed_voltage = 13.0;

void setup() {
  analogReadResolution(12); // Resolução nativa do Nano 33 BLE
  Serial.begin(115200);   
  Serial1.begin(115200);  
  BARO.begin();
  
  setup_motor(mLeft);
  setup_motor(mRight);

  stop_all_motors();
  
  StaticJsonDocument<100> doc;
  doc["type"] = "INIT";
  doc["status"] = "ready";
  serializeJson(doc, Serial);
  Serial.println();
}

void loop() {
  if (Serial.available()) {
    process_serial_command();
  }

  while (Serial1.available()) {
    gps.encode(Serial1.read());
  }

  if (millis() - last_gps_send > 1000) {
    send_gps_data();
    last_gps_send = millis();
  }

  if (millis() - last_battery_send > 5000) {
    send_battery_data();
    last_battery_send = millis();
  }

  if (command_timeout != 0 && millis() > command_timeout) {
    stop_all_motors();
    command_timeout = 0;
  }
}

// --- Lógica de Movimento ---
void setup_motor(MotorPins m) {
  pinMode(m.rpwm, OUTPUT);
  pinMode(m.lpwm, OUTPUT);
  pinMode(m.ren, OUTPUT);
  pinMode(m.len, OUTPUT);
  digitalWrite(m.ren, HIGH);
  digitalWrite(m.len, HIGH);
}

void drive_motor(MotorPins m, int speed) {
  int pwmValue = constrain(abs(speed), 0, 255);
  if (speed > 0) { 
    analogWrite(m.lpwm, pwmValue);
    analogWrite(m.rpwm, 0);
  } else if (speed < 0) { 
    analogWrite(m.lpwm, 0);
    analogWrite(m.rpwm, pwmValue);
  } else { 
    analogWrite(m.lpwm, 0);
    analogWrite(m.rpwm, 0);
  }
}

void stop_all_motors() {
  drive_motor(mLeft, 0);
  drive_motor(mRight, 0);
}

void process_serial_command() {
  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, Serial);
  if (error) return;

  String cmd = doc["cmd"];
  if (cmd == "MOVE") {
    drive_motor(mLeft, doc["wheels"]["L"]);
    drive_motor(mRight, doc["wheels"]["R"]);
    uint16_t duration = doc["duration"] | 1000;
    command_timeout = millis() + duration;
    send_ack("MOVE");
  } 
  else if (cmd == "STOP") {
    stop_all_motors();
    send_ack("STOP");
  }
}

// --- Telemetria Restaurada ---
void send_gps_data() {
  StaticJsonDocument<400> out;
  out["type"] = "GPS";
  out["is_valid"] = gps.location.isValid();
  out["latitude"] = gps.location.lat();
  out["longitude"] = gps.location.lng();
  out["altitude"] = gps.location.alt();
  out["satellites"] = gps.satellites.value();
  out["hdop"] = gps.hdop.hdop();
  
  if (gps.time.isValid()) {
    char time_str[12];
    sprintf(time_str, "%02d:%02d:%02d", gps.time.hour(), gps.time.minute(), gps.time.second());
    out["timestamp"] = time_str;
  } else {
    out["timestamp"] = "";
  }
  
  serializeJson(out, Serial);
  Serial.println();
}

void send_battery_data() {
  int raw_v = analogRead(battery_voltage_pin);
  float instant_v = (raw_v / 4095.0) * ADC_REF * DIVISOR_RATIO;
  smoothed_voltage = (smoothed_voltage * 0.9) + (instant_v * 0.1);

  int raw_i = analogRead(current_sensor_pin);
  float voltage_i = (raw_i / 4095.0) * 5.0; 
  float current = (voltage_i - CURRENT_ZERO_V) / CURRENT_SENS;
  float pct = ((smoothed_voltage - BATT_MIN) / (BATT_MAX - BATT_MIN)) * 100.0;

  StaticJsonDocument<300> out;
  out["type"] = "BATTERY";
  out["voltage"] = smoothed_voltage;
  out["percentage"] = constrain(pct, 0, 100);
  out["current"] = current;
  out["temp"] = BARO.readTemperature();
  out["is_charging"] = (current < -0.15); 
  
  serializeJson(out, Serial);
  Serial.println();
}

void send_ack(const char* cmd) {
  StaticJsonDocument<64> out;
  out["type"] = "ACK";
  out["cmd"] = cmd;
  serializeJson(out, Serial);
  Serial.println();
}