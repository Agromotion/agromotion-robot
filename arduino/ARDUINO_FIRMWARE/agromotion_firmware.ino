#include <ArduinoJson.h>
#include <TinyGPS++.h>
#include <HardwareSerial.h>
#include <Wire.h>
#include <VL53L0X.h>

// =======================
// FORWARD DECLARATIONS
// =======================
void process_serial_command();
void enviarTelemetriaGPS();
void enviarTelemetriaBateria();
void enviarTelemetriaSensores();
void stop_all_motors();
void drive_motor(struct MotorPins m, int speed);
void drive_drum(int speed);
void setup_motor(struct MotorPins m);
void set_auto_mode(bool enabled);
void gerir_modo_automatico();
void ler_sensores_proximidade();
void clique_pirilampo();
void ativar_pirilampo();
void desligar_pirilampo();
void setup_sensor_obstaculo();
bool obstaculo_frontal_detetado();

// =======================
// CONFIG
// =======================
#define PIN_BATERIA 1

const float ADC_REF = 3.3;
const float ADC_MAX = 4095.0;
const float DIVISOR = 5.0;
const float CALIBRACAO = 1.036;

static const int GPS_RX_PIN = 19;
static const int GPS_TX_PIN = 20;

static const int SENSOR_DIREITA = 40;
static const int SENSOR_MEIO = 41;
static const int SENSOR_ESQUERDA = 42;

// Sensor obstáculo VL53L0X
#define VL53_SDA 38
#define VL53_SCL 37
const int DISTANCIA_OBSTACULO_MM = 100;

const unsigned long TEMPO_RECUPERACAO = 8000;

const int AUTO_RETO = 255;
const int AUTO_CURVA_SUAVE_RAPIDO = 155;
const int AUTO_CURVA_SUAVE_LENTO = 110;
const int AUTO_CURVA_FORTE_RAPIDO = 200;
const int AUTO_CURVA_FORTE_LENTO = 70;
const int AUTO_TAMBOR = 80;

static const int PIN_PIRILAMPO = 48;
const unsigned long TEMPO_CLIQUE_PIRILAMPO = 250;

// =======================
// STRUCTS
// =======================
struct MotorPins {
  int rpwm, lpwm, ren, len;
};

// =======================
// MOTORS
// =======================
MotorPins mLeft  = {5, 6, 7, 8};
MotorPins mRight = {9, 10, 11, 12};
MotorPins mDrum  = {15, 16, 17, 18};

// =======================
// STATE
// =======================
TinyGPSPlus gps;
HardwareSerial GPSSerial(1);
VL53L0X sensorObstaculo;

unsigned long command_timeout = 0;
unsigned long last_battery_send = 0;
unsigned long last_gps_send = 0;
unsigned long last_sensor_send = 0;
unsigned long ultimoMovimento = 0;

bool motoresEmMovimento = false;
bool autoModeEnabled = false;

unsigned long last_auto_action = 0;

bool obstaculoDireita = false;
bool obstaculoMeio = false;
bool obstaculoEsquerda = false;

bool sensorObstaculoOK = false;
int distanciaObstaculoMM = 9999;
bool obstaculoFrontal = false;

float tensaoFiltrada = 0;
float percentagemAtual = 0;

// =======================
// SETUP
// =======================
void setup() {
  delay(1000);
  Serial.begin(115200);

  GPSSerial.begin(9600, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  setup_motor(mLeft);
  setup_motor(mRight);
  setup_motor(mDrum);

  pinMode(SENSOR_DIREITA, INPUT_PULLUP);
  pinMode(SENSOR_MEIO, INPUT_PULLUP);
  pinMode(SENSOR_ESQUERDA, INPUT_PULLUP);

  setup_sensor_obstaculo();

  stop_all_motors();

  Serial.println("{\"type\":\"INIT\",\"status\":\"ready\"}");

  pinMode(PIN_PIRILAMPO, OUTPUT);
  digitalWrite(PIN_PIRILAMPO, LOW);
}

// =======================
// LOOP
// =======================
void loop() {
  if (Serial.available() > 0) {
    process_serial_command();
  }

  while (GPSSerial.available()) {
    gps.encode(GPSSerial.read());
  }

  if (autoModeEnabled) {
    gerir_modo_automatico();
  }

  if (millis() - last_gps_send > 1000) {
    enviarTelemetriaGPS();
    last_gps_send = millis();
  }

  if (millis() - last_sensor_send > 1000) {
    enviarTelemetriaSensores();
    last_sensor_send = millis();
  }

  if (millis() - last_battery_send > 5000) {
    enviarTelemetriaBateria();
    last_battery_send = millis();
  }

  if (command_timeout != 0 && millis() > command_timeout) {
    stop_all_motors();
    command_timeout = 0;
  }
}

// =======================
// SERIAL COMMANDS
// =======================
void process_serial_command() {
  if (!Serial.available()) return;

  String input = Serial.readStringUntil('\n');
  input.trim();

  if (input.length() == 0 || input[0] != '{') return;

  StaticJsonDocument<512> doc;

  if (deserializeJson(doc, input)) {
    Serial.println("{\"type\":\"ERR\",\"msg\":\"JSON_PARSE_FAIL\"}");
    return;
  }

  const char* cmd = doc["cmd"] | "";

  if (strcmp(cmd, "MIXED_CONTROL") == 0) {
    if (autoModeEnabled) {
      Serial.println("{\"type\":\"ACK\",\"cmd\":\"MIXED_CONTROL\"}");
      return;
    }

    int left  = doc["left"]  | 0;
    int right = doc["right"] | 0;
    int drum  = doc["drum"]  | 0;

    drive_motor(mLeft, left);
    drive_motor(mRight, right);
    drive_drum(drum);

    motoresEmMovimento = (left != 0 || right != 0 || drum != 0);
    if (motoresEmMovimento) ultimoMovimento = millis();

    Serial.println("{\"type\":\"ACK\",\"cmd\":\"MIXED_CONTROL\"}");
    return;
  }

  if (strcmp(cmd, "MOVE") == 0) {
    if (autoModeEnabled) {
      Serial.println("{\"type\":\"ACK\",\"cmd\":\"MOVE\"}");
      return;
    }

    int speedL = doc["wheels"]["L"] | 0;
    int speedR = doc["wheels"]["R"] | 0;

    drive_motor(mLeft, speedL);
    drive_motor(mRight, speedR);

    command_timeout = millis() + (doc["duration"] | 300);
    motoresEmMovimento = true;

    Serial.println("{\"type\":\"ACK\",\"cmd\":\"MOVE\"}");
    return;
  }

  if (strcmp(cmd, "DRUM") == 0) {
    if (autoModeEnabled) {
      Serial.println("{\"type\":\"ACK\",\"cmd\":\"DRUM\"}");
      return;
    }

    int speed = doc["speed"] | 0;
    drive_drum(speed);

    Serial.println("{\"type\":\"ACK\",\"cmd\":\"DRUM\"}");
    return;
  }

  if (strcmp(cmd, "STOP") == 0) {
    stop_all_motors();
    command_timeout = 0;

    Serial.println("{\"type\":\"ACK\",\"cmd\":\"STOP\"}");
    return;
  }

  if (strcmp(cmd, "AUTO_MODE") == 0) {
    bool enabled = doc["enabled"] | false;
    set_auto_mode(enabled);

    Serial.println("{\"type\":\"ACK\",\"cmd\":\"AUTO_MODE\"}");
    return;
  }

  Serial.println("{\"type\":\"ERR\",\"msg\":\"UNKNOWN_CMD\"}");
}

// =======================
// MOTORS
// =======================
void setup_motor(MotorPins m) {
  pinMode(m.rpwm, OUTPUT);
  pinMode(m.lpwm, OUTPUT);
  pinMode(m.ren, OUTPUT);
  pinMode(m.len, OUTPUT);

  digitalWrite(m.ren, HIGH);
  digitalWrite(m.len, HIGH);
}

void drive_motor(MotorPins m, int speed) {
  int pwm = constrain(abs(speed), 0, 255);

  if (speed > 0) {
    analogWrite(m.rpwm, 0);
    analogWrite(m.lpwm, pwm);
  } else if (speed < 0) {
    analogWrite(m.lpwm, 0);
    analogWrite(m.rpwm, pwm);
  } else {
    analogWrite(m.lpwm, 0);
    analogWrite(m.rpwm, 0);
  }
}

void drive_drum(int speed) {
  int pwm = constrain(abs(speed), 0, 255);

  if (speed > 0) {
    analogWrite(mDrum.rpwm, 0);
    analogWrite(mDrum.lpwm, pwm);
  } else if (speed < 0) {
    analogWrite(mDrum.lpwm, 0);
    analogWrite(mDrum.rpwm, pwm);
  } else {
    analogWrite(mDrum.lpwm, 0);
    analogWrite(mDrum.rpwm, 0);
  }
}

void stop_all_motors() {
  drive_motor(mLeft, 0);
  drive_motor(mRight, 0);
  drive_drum(0);

  if (motoresEmMovimento) {
    motoresEmMovimento = false;
    ultimoMovimento = millis();
  }
}

void set_auto_mode(bool enabled) {
  if (autoModeEnabled == enabled) return;

  autoModeEnabled = enabled;
  command_timeout = 0;
  stop_all_motors();

  if (enabled) {
    ativar_pirilampo();
    last_auto_action = millis();
    Serial.println("{\"type\":\"INFO\",\"msg\":\"AUTO_MODE_STARTED\"}");
  } else {
    desligar_pirilampo();
    Serial.println("{\"type\":\"INFO\",\"msg\":\"AUTO_MODE_STOPPED\"}");
  }
}

// =======================
// MODO AUTOMATICO E SENSORES
// =======================
void ler_sensores_proximidade() {
  obstaculoDireita = (digitalRead(SENSOR_DIREITA) == LOW);
  obstaculoMeio = (digitalRead(SENSOR_MEIO) == LOW);
  obstaculoEsquerda = (digitalRead(SENSOR_ESQUERDA) == LOW);
}

void gerir_modo_automatico() {
  if (millis() - last_auto_action < 100) return;
  last_auto_action = millis();

  if (obstaculo_frontal_detetado()) {
    stop_all_motors();
    return;
  }

  ler_sensores_proximidade();

  if (obstaculoMeio && !obstaculoEsquerda && !obstaculoDireita) {
    drive_motor(mLeft, AUTO_RETO);
    drive_motor(mRight, AUTO_RETO);
    drive_drum(0);
    motoresEmMovimento = true;
    return;
  }

  if (obstaculoMeio && obstaculoEsquerda && !obstaculoDireita) {
    drive_motor(mLeft, AUTO_CURVA_SUAVE_LENTO);
    drive_motor(mRight, AUTO_CURVA_SUAVE_RAPIDO);
    drive_drum(0);
    motoresEmMovimento = true;
    return;
  }

  if (obstaculoMeio && !obstaculoEsquerda && obstaculoDireita) {
    drive_motor(mLeft, AUTO_CURVA_SUAVE_RAPIDO);
    drive_motor(mRight, AUTO_CURVA_SUAVE_LENTO);
    drive_drum(0);
    motoresEmMovimento = true;
    return;
  }

  if (!obstaculoMeio && obstaculoEsquerda && !obstaculoDireita) {
    drive_motor(mLeft, AUTO_CURVA_FORTE_LENTO);
    drive_motor(mRight, AUTO_CURVA_FORTE_RAPIDO);
    drive_drum(0);
    motoresEmMovimento = true;
    return;
  }

  if (!obstaculoMeio && !obstaculoEsquerda && obstaculoDireita) {
    drive_motor(mLeft, AUTO_CURVA_FORTE_RAPIDO);
    drive_motor(mRight, AUTO_CURVA_FORTE_LENTO);
    drive_drum(0);
    motoresEmMovimento = true;
    return;
  }

  if (obstaculoEsquerda && obstaculoMeio && obstaculoDireita) {
    drive_motor(mLeft, AUTO_RETO);
    drive_motor(mRight, AUTO_RETO);
    drive_drum(0);
    motoresEmMovimento = true;
    return;
  }

  stop_all_motors();
}

// =======================
// SENSOR DE OBSTACULO VL53L0X
// =======================
void setup_sensor_obstaculo() {
  Wire.begin(VL53_SDA, VL53_SCL);
  Wire.setClock(100000);

  sensorObstaculo.setTimeout(100);

  if (sensorObstaculo.init()) {
    sensorObstaculoOK = true;
    sensorObstaculo.startContinuous();
    Serial.println("{\"type\":\"INFO\",\"msg\":\"VL53L0X_OK\"}");
  } else {
    sensorObstaculoOK = false;
    Serial.println("{\"type\":\"ERR\",\"msg\":\"VL53L0X_FAIL\"}");
  }
}

bool obstaculo_frontal_detetado() {
  if (!sensorObstaculoOK) {
    obstaculoFrontal = false;
    return false;
  }

  distanciaObstaculoMM = sensorObstaculo.readRangeContinuousMillimeters();

  if (sensorObstaculo.timeoutOccurred()) {
    obstaculoFrontal = false;
    return false;
  }

  obstaculoFrontal = (distanciaObstaculoMM > 30 && distanciaObstaculoMM <= DISTANCIA_OBSTACULO_MM);
  return obstaculoFrontal;
}

// =======================
// TELEMETRY
// =======================
void enviarTelemetriaGPS() {
  StaticJsonDocument<200> doc;
  doc["type"] = "GPS";
  doc["is_valid"] = gps.location.isValid();

  if (gps.location.isValid()) {
    doc["latitude"] = gps.location.lat();
    doc["longitude"] = gps.location.lng();
  }

  serializeJson(doc, Serial);
  Serial.println();
}

float calcularPercentagemBateria(float v, bool emCarga) {
  // Compensação simples: com motores ativos a tensão cai
  if (emCarga) {
    v += 0.25;
  }

  if (v >= 12.75) return 100;
  if (v >= 12.60) return 90;
  if (v >= 12.40) return 70;
  if (v >= 12.20) return 40;
  if (v >= 12.00) return 20;
  if (v >= 11.80) return 10;
  return 0;
}

void enviarTelemetriaBateria() {
  int adc = analogRead(PIN_BATERIA);

  float tensaoPino = (adc / ADC_MAX) * ADC_REF;
  float tensaoBateria = tensaoPino * DIVISOR * CALIBRACAO;

  if (tensaoFiltrada <= 0) {
    tensaoFiltrada = tensaoBateria;
  } else {
    tensaoFiltrada = (tensaoFiltrada * 0.85) + (tensaoBateria * 0.15);
  }

  float percentagem = calcularPercentagemBateria(tensaoFiltrada, motoresEmMovimento);
  percentagemAtual = percentagem;

  StaticJsonDocument<200> doc;
  doc["type"] = "BATTERY";
  doc["voltage"] = tensaoFiltrada;
  doc["percentage"] = (int)percentagemAtual;
  doc["is_moving"] = motoresEmMovimento;
  doc["auto_mode"] = autoModeEnabled;

  serializeJson(doc, Serial);
  Serial.println();
}

void enviarTelemetriaSensores() {
  StaticJsonDocument<200> doc;
  doc["type"] = "SENSORS";
  doc["left"] = obstaculoEsquerda;
  doc["center"] = obstaculoMeio;
  doc["right"] = obstaculoDireita;
  doc["obstacle"] = obstaculoFrontal;
  doc["distance_mm"] = distanciaObstaculoMM;

  serializeJson(doc, Serial);
  Serial.println();
}

void clique_pirilampo() {
  digitalWrite(PIN_PIRILAMPO, HIGH);
  delay(TEMPO_CLIQUE_PIRILAMPO);
  digitalWrite(PIN_PIRILAMPO, LOW);
  delay(TEMPO_CLIQUE_PIRILAMPO);
}

void ativar_pirilampo() {
  clique_pirilampo();
  clique_pirilampo();
  clique_pirilampo();
}

void desligar_pirilampo() {
  clique_pirilampo();
}
