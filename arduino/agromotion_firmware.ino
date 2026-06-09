#include <ArduinoJson.h>
#include <TinyGPS++.h>
#include <HardwareSerial.h>

// =======================
// FORWARD DECLARATIONS
// =======================
void process_serial_command();
void enviarTelemetriaGPS();
void enviarTelemetriaBateria();
void stop_all_motors();
void drive_motor(struct MotorPins m, int speed);
void drive_drum(int speed);
void setup_motor(struct MotorPins m);

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

const unsigned long TEMPO_RECUPERACAO = 8000;

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

unsigned long command_timeout = 0;
unsigned long last_battery_send = 0;
unsigned long last_gps_send = 0;
unsigned long ultimoMovimento = 0;

bool motoresEmMovimento = false;

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

  stop_all_motors();

  Serial.println("{\"type\":\"INIT\",\"status\":\"ready\"}");
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

  if (millis() - last_gps_send > 1000) {
    enviarTelemetriaGPS();
    last_gps_send = millis();
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

  // =========================
  // MIXED CONTROL (PI)
  // =========================
  if (strcmp(cmd, "MIXED_CONTROL") == 0) {

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

  // MOVE legacy
  if (strcmp(cmd, "MOVE") == 0) {
    int speedL = doc["wheels"]["L"] | 0;
    int speedR = doc["wheels"]["R"] | 0;

    drive_motor(mLeft, speedL);
    drive_motor(mRight, speedR);

    command_timeout = millis() + (doc["duration"] | 300);
    motoresEmMovimento = true;

    Serial.println("{\"type\":\"ACK\",\"cmd\":\"MOVE\"}");
    return;
  }

  // DRUM legacy
  if (strcmp(cmd, "DRUM") == 0) {
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

// =======================
// TELEMETRY (placeholders ok)
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

void enviarTelemetriaBateria() {
  StaticJsonDocument<200> doc;
  doc["type"] = "BATTERY";
  doc["voltage"] = 12.5;
  doc["percentage"] = 80;
  doc["is_moving"] = motoresEmMovimento;

  serializeJson(doc, Serial);
  Serial.println();
}
