#include <ArduinoJson.h>
#include <TinyGPS++.h>
#include <Arduino_LPS22HB.h> // Biblioteca para o sensor de pressão/temp do Nano 33 BLE

// --- Configuração ---
const float BATT_MAX = 14.0;         // 100%
const float BATT_MIN = 10.0;         // 0%
const float DIVISOR_RATIO = 5.0;     // Vin / Vout (calculado: 13.63/2.726)
const float ADC_REF = 3.3;           // Voltagem de referência do Nano 33 Sense
const float CURRENT_ZERO_V = 2.5;    // Centro do ACS712 (0A costuma ser 2.5V em sistemas 5V)
const float CURRENT_SENS = 0.100;    // Sensibilidade (ex: 100mV/A para o modelo 20A)

// --- Estruturas de Hardware ---
struct MotorPins {
  int en, in1, in2;
};
MotorPins mFL = {2, 3, 4};
MotorPins mFR = {5, 6, 7};
MotorPins mREAR = {9, 10, 11};

const int battery_voltage_pin = A0;
const int current_sensor_pin = A1;

// --- Variáveis de Estado ---
TinyGPSPlus gps;
unsigned long command_timeout = 0;
unsigned long last_gps_send = 0;
unsigned long last_battery_send = 0;
float smoothed_voltage = 13.0; // Valor inicial médio

void setup() {
  // O Nano 33 BLE Sense Rev2 tem ADCs de alta resolução
  analogReadResolution(12); 
  
  Serial.begin(115200);   // USB para Raspberry Pi
  Serial1.begin(115200);  // Grove Air530 GPS
  BARO.begin();
  
  pinMode(mFL.en, OUTPUT); pinMode(mFL.in1, OUTPUT); pinMode(mFL.in2, OUTPUT);
  pinMode(mFR.en, OUTPUT); pinMode(mFR.in1, OUTPUT); pinMode(mFR.in2, OUTPUT);
  pinMode(mREAR.en, OUTPUT); pinMode(mREAR.in1, OUTPUT); pinMode(mREAR.in2, OUTPUT);

  stop_all_motors();
  
  // Feedback visual de arranque
  StaticJsonDocument<100> doc;
  doc["type"] = "INIT";
  doc["status"] = "ready";
  serializeJson(doc, Serial);
  Serial.println();
}

void loop() {
  // 1. Processar Comandos do Pi
  if (Serial.available()) {
    process_serial_command();
  }

  // 2. Ler GPS (Stream constante)
  while (Serial1.available()) {
    gps.encode(Serial1.read());
  }

  // 3. Enviar Telemetria GPS (1s)
  if (millis() - last_gps_send > 1000) {
    send_gps_data();
    last_gps_send = millis();
  }

  // 4. Enviar Telemetria da Bateria (5s)
  if (millis() - last_battery_send > 5000) {
    send_battery_data();
    last_battery_send = millis();
  }

  // 5. Safety Timeout (Failsafe)
  if (millis() > command_timeout && command_timeout != 0) {
    stop_all_motors();
    command_timeout = 0;
  }
}

// --- Processamento de Comandos do Pi ---
void process_serial_command() {
  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, Serial);
  if (error) return;

  String cmd = doc["cmd"];
  if (cmd == "MOVE") {
    // Comando vem do command_handler.py
    // Aceita valores de -255 a 255 para suportar rotação/marcha-atrás
    control_motor(mFL, doc["wheels"]["FL"]);
    control_motor(mFR, doc["wheels"]["FR"]);
    control_motor(mREAR, doc["wheels"]["REAR"]);
    
    uint16_t duration = doc["duration"] | 500;
    command_timeout = millis() + duration;
    send_ack("MOVE");
  } 
  else if (cmd == "STOP") {
    stop_all_motors();
    send_ack("STOP");
  }
  else if (cmd == "PING") {
    send_ack("PONG");
  }
}

// --- Controlo de Hardware real ---
void control_motor(MotorPins m, int speed) {
  if (speed > 0) { // Frente
    digitalWrite(m.in1, HIGH);
    digitalWrite(m.in2, LOW);
    analogWrite(m.en, constrain(speed, 0, 255));
  } else if (speed < 0) { // Trás
    digitalWrite(m.in1, LOW);
    digitalWrite(m.in2, HIGH);
    analogWrite(m.en, constrain(abs(speed), 0, 255));
  } else { // Parar
    digitalWrite(m.in1, LOW);
    digitalWrite(m.in2, LOW);
    analogWrite(m.en, 0);
  }
}

void stop_all_motors() {
  control_motor(mFL, 0);
  control_motor(mFR, 0);
  control_motor(mREAR, 0);
}

// --- Envio de Telemetria ---
void send_gps_data() {
  StaticJsonDocument<300> out;
  out["type"] = "GPS";
  out["is_valid"] = gps.location.isValid();
  out["latitude"] = gps.location.lat();
  out["longitude"] = gps.location.lng();
  out["altitude"] = gps.location.alt();
  out["satellites"] = gps.satellites.value();
  out["hdop"] = gps.hdop.hdop();
  
  // Formatando timestamp para ISO ou similar
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
  // Leitura de Voltagem
  int raw_v = analogRead(battery_voltage_pin);
  float instant_v = (raw_v / 4095.0) * ADC_REF * DIVISOR_RATIO;
  
  // Suavização (Filtro Passa-Baixo) para ignorar ruído dos motores
  smoothed_voltage = (smoothed_voltage * 0.9) + (instant_v * 0.1);

  // Leitura de Corrente (ACS712)
  int raw_i = analogRead(current_sensor_pin);
  float voltage_i = (raw_i / 4095.0) * 5.0; // ACS712 costuma operar a 5V
  float current = (voltage_i - CURRENT_ZERO_V) / CURRENT_SENS;
  float internal_temp = BARO.readTemperature();

  // Cálculo de Percentagem (Escala NiMH 10V-14V)
  float pct = ((smoothed_voltage - BATT_MIN) / (BATT_MAX - BATT_MIN)) * 100.0;

  StaticJsonDocument<256> out;
  out["type"] = "BATTERY";
  out["voltage"] = smoothed_voltage;
  out["percentage"] = constrain(pct, 0, 100);
  out["current"] = current;
  
  // Se a corrente for negativa, está a carregar
  out["is_charging"] = (current < -0.15); 
  
  out["temperature"] = internal_temp;
  
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