#include "esp_camera.h"
#include <WiFi.h>
#include "esp_http_server.h"

const char* ssid = "Vlsi";
const char* password = "vlsi80211";

#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22
#define FLASH_LED_PIN      4

static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=frame";
static const char* STREAM_BOUNDARY = "\r\n--frame\r\n";
static const char* STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

httpd_handle_t camera_httpd = NULL;
bool flashEnabled = false;

void setFlashState(bool enabled) {
  flashEnabled = enabled;
  digitalWrite(FLASH_LED_PIN, enabled ? HIGH : LOW);
}

void startCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 10;
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.frame_size = FRAMESIZE_CIF;
    config.jpeg_quality = 12;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return;
  }

  sensor_t* sensor = esp_camera_sensor_get();
  sensor->set_brightness(sensor, 1);
  sensor->set_contrast(sensor, 1);
  sensor->set_saturation(sensor, 1);
}

static esp_err_t index_handler(httpd_req_t* req) {
  String html = "<!doctype html><html><head><title>ESP32 Traffic Camera</title></head><body>";
  html += "<h2>ESP32 Traffic Camera Online</h2>";
  html += "<p>Live stream: <a href=\"/stream\">/stream</a></p>";
  html += "<p>Snapshot: <a href=\"/jpg\">/jpg</a></p>";
  html += "<p>Flash on: <a href=\"/flash?state=on\">/flash?state=on</a></p>";
  html += "<p>Flash off: <a href=\"/flash?state=off\">/flash?state=off</a></p>";
  html += "</body></html>";
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, html.c_str(), html.length());
}

static esp_err_t jpg_handler(httpd_req_t* req) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }

  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t result = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return result;
}

static esp_err_t stream_handler(httpd_req_t* req) {
  char part_buffer[64];
  httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-cache");

  while (true) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Stream capture failed");
      return ESP_FAIL;
    }

    size_t header_len = snprintf(part_buffer, sizeof(part_buffer), STREAM_PART, fb->len);
    esp_err_t result = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (result == ESP_OK) result = httpd_resp_send_chunk(req, part_buffer, header_len);
    if (result == ESP_OK) result = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);

    esp_camera_fb_return(fb);

    if (result != ESP_OK) {
      break;
    }

    delay(30);
  }

  return ESP_OK;
}

static esp_err_t flash_handler(httpd_req_t* req) {
  char query[64];
  char state[16];
  bool hasState = false;

  if (httpd_req_get_url_query_len(req) > 0 && httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK) {
    if (httpd_query_key_value(query, "state", state, sizeof(state)) == ESP_OK) {
      hasState = true;
    }
  }

  if (hasState) {
    String requested = String(state);
    requested.toLowerCase();
    if (requested == "on" || requested == "1" || requested == "true") {
      setFlashState(true);
    } else if (requested == "off" || requested == "0" || requested == "false") {
      setFlashState(false);
    }
  }

  String json = "{\"flash_enabled\":";
  json += flashEnabled ? "true" : "false";
  json += "}";

  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, json.c_str(), json.length());
}

void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.ctrl_port = 32768;
  config.max_open_sockets = 5;

  httpd_uri_t index_uri = {
    .uri = "/",
    .method = HTTP_GET,
    .handler = index_handler,
    .user_ctx = NULL
  };

  httpd_uri_t jpg_uri = {
    .uri = "/jpg",
    .method = HTTP_GET,
    .handler = jpg_handler,
    .user_ctx = NULL
  };

  httpd_uri_t stream_uri = {
    .uri = "/stream",
    .method = HTTP_GET,
    .handler = stream_handler,
    .user_ctx = NULL
  };

  httpd_uri_t flash_uri = {
    .uri = "/flash",
    .method = HTTP_GET,
    .handler = flash_handler,
    .user_ctx = NULL
  };

  if (httpd_start(&camera_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(camera_httpd, &index_uri);
    httpd_register_uri_handler(camera_httpd, &jpg_uri);
    httpd_register_uri_handler(camera_httpd, &stream_uri);
    httpd_register_uri_handler(camera_httpd, &flash_uri);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(FLASH_LED_PIN, OUTPUT);
  setFlashState(false);

  startCamera();

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("WiFi connected. Camera URL: http://");
  Serial.println(WiFi.localIP());

  startCameraServer();
  Serial.print("Live stream ready at: http://");
  Serial.print(WiFi.localIP());
  Serial.println("/stream");
}

void loop() {
  delay(1000);
}
