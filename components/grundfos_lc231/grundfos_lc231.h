#pragma once

#include <array>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/component.h"

#ifdef USE_ESP32
#include <esp_gap_ble_api.h>
#include <esp_gattc_api.h>
#endif

namespace esphome::grundfos_lc231 {

enum BaseSensor : uint8_t {
  WATER_LEVEL = 0,
  CIO1_LEVEL,
  CIO1_INPUT_CURRENT,
  CONTROLLER_TOTAL_ON_TIME,
  CONTROLLER_ACTIVE_PUMPING_TIME,
  MAINS_PHASE_TO_NEUTRAL_VOLTAGE,
  MAINS_PHASE_TO_PHASE_VOLTAGE,
  MAINS_UNIDENTIFIED_VOLTAGE,
  UNIDENTIFIED_TEMPERATURE,
  PUMP_1_TEMPERATURE,
  PUMP_2_TEMPERATURE,
  HIGH_LEVEL_AFTER_RUN_TIME,
  STOP_DELAY_TIME,
  FORCED_START_TIME,
  INTER_PUMP_DELAY,
  MAXIMUM_RUNNING_PUMPS,
  MAXIMUM_RUN_TIME,
  PUMP_PAUSE_TIME,
  RTC_UNIDENTIFIED_BYTE_10,
  PROTOCOL_ERRORS,
  BASE_SENSOR_COUNT,
};

enum PumpSensor : uint8_t {
  LIFETIME_RUNS = 0,
  RUNS_LAST_HOUR,
  RUNS_LAST_24_HOURS,
  LIFETIME_RUNTIME,
  RUNTIME_LAST_24_HOURS,
  LAST_RUN_DURATION,
  RUNS_TODAY,
  RUNS_YESTERDAY,
  RUNTIME_TODAY,
  RUNTIME_YESTERDAY,
  LIVE_VOLTAGE,
  LIVE_CURRENT,
  LIVE_POWER,
  LIVE_POWER_FACTOR,
  LAST_RUN_CURRENT,
  LAST_RUN_POWER_FACTOR,
  LAST_RUN_POWER,
  PUMP_SENSOR_COUNT,
};

enum BinarySensorIndex : uint8_t {
  CONNECTED = 0,
  ABOVE_STOP_LEVEL,
  PUMP_1_RUNNING,
  PUMP_2_RUNNING,
  ACTIVE_ALARM,
  ACTIVE_WARNING,
  AUTOMATIC_TIME_SETTING,
  RUN_AND_PAUSE_ENABLED,
  BINARY_SENSOR_COUNT,
};

enum TextSensorIndex : uint8_t {
  PUMP_1_CONTROL_SELECTION = 0,
  PUMP_2_CONTROL_SELECTION,
  MANUAL_START_RETURN_MODE,
  ACTIVE_ALARMS_RAW,
  ACTIVE_WARNINGS_RAW,
  LAST_ALARM,
  LAST_WARNING,
  LAST_ALARM_TIME,
  LAST_WARNING_TIME,
  PROTOCOL_STATUS,
  PRODUCT_TYPE,
  PRODUCT_NUMBER,
  SERIAL_NUMBER,
  PRODUCTION_CODE,
  APPLICATION_SOFTWARE,
  MOTOR_CONTROL_SOFTWARE,
  BLE_SOFTWARE,
  SYSTEM_NAME,
  GSC_VERSION,
  CONTROLLER_LOCAL_TIME,
  LAST_CLOCK_SYNCHRONIZATION,
  DST_SCHEDULE,
  STANDARD_UTC_OFFSET,
  LEVEL_CONTROL_PREFIX_RAW,
  CLOCK_RECORD_5E0000_RAW,
  CLOCK_RECORD_5E0001_RAW,
  CLOCK_RECORD_5E0002_RAW,
  CLOCK_RECORD_5E0003_RAW,
  CLOCK_RECORD_5E0004_RAW,
  TEXT_SENSOR_COUNT,
};

enum class RequestKind : uint8_t {
  OBJECT = 0,
  STRING,
  EVENT_SCAN_COMPLETE,
};

struct RequestSpec {
  RequestKind kind;
  uint32_t id;
  int32_t expected_meta;
  uint16_t expected_type_version;
  int16_t expected_data_length;
};

struct EventRecord {
  uint16_t event_id;
  uint16_t code;
  uint8_t source;
  uint8_t device;
  uint8_t condition;
  uint32_t timestamp;
  uint16_t detail;
};

class GrundfosLC231Component : public Component, public ble_client::BLEClientNode {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;

#ifdef USE_ESP32
  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override;
  void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) override;
#endif

  void set_update_intervals(uint32_t fast, uint32_t regular, uint32_t slow) {
    this->fast_update_interval_ = fast;
    this->regular_update_interval_ = regular;
    this->slow_update_interval_ = slow;
  }

  void set_sensor(uint8_t index, sensor::Sensor *entity) {
    if (index < BASE_SENSOR_COUNT)
      this->sensors_[index] = entity;
  }
  void set_pump_sensor(uint8_t pump, uint8_t index, sensor::Sensor *entity) {
    if (pump < 2 && index < PUMP_SENSOR_COUNT)
      this->pump_sensors_[pump][index] = entity;
  }
  void set_binary_sensor(uint8_t index, binary_sensor::BinarySensor *entity) {
    if (index < BINARY_SENSOR_COUNT)
      this->binary_sensors_[index] = entity;
  }
  void set_text_sensor(uint8_t index, text_sensor::TextSensor *entity) {
    if (index < TEXT_SENSOR_COUNT)
      this->text_sensors_[index] = entity;
  }

 protected:
  void reset_connection_state_();
  void register_for_notifications_();
  void become_ready_();
  void schedule_requests_(uint32_t now);
  void enqueue_identification_();
  void enqueue_session_snapshot_();
  void enqueue_fast_();
  void enqueue_regular_();
  void enqueue_slow_();
  void enqueue_event_scan_();
  bool send_next_request_();
  void finish_request_();
  void record_error_(const std::string &message);

  void feed_notification_(const uint8_t *data, size_t length);
  void handle_frame_(const std::vector<uint8_t> &frame);
  bool validate_reply_(const std::vector<uint8_t> &frame, const RequestSpec &request,
                       const uint8_t **data, size_t *data_length);
  void decode_string_(uint8_t string_id, const uint8_t *data, size_t length);
  void decode_object_(uint32_t object_id, const uint8_t *data, size_t length);
  void decode_active_conditions_(bool alarm, const uint8_t *data, size_t length);
  void decode_pump_counters_(uint8_t pump, const uint8_t *data, size_t length);
  void decode_pump_daily_(uint8_t pump, const uint8_t *data, size_t length);
  void decode_live_electrical_(uint8_t pump, const uint8_t *data, size_t length);
  void decode_last_run_electrical_(uint8_t pump, const uint8_t *data, size_t length);
  void decode_event_(const uint8_t *data, size_t length);
  void publish_latest_events_();
  void publish_event_(const EventRecord &event);
  void publish_controller_local_time_();
  void publish_last_clock_synchronization_();

  void publish_sensor_(BaseSensor index, float value);
  void publish_pump_sensor_(uint8_t pump, PumpSensor index, float value);
  void publish_binary_(BinarySensorIndex index, bool value);
  void publish_text_(TextSensorIndex index, const std::string &value);

  static uint16_t crc16_ccitt_(const uint8_t *data, size_t length);
  static uint16_t read_u16_(const uint8_t *data);
  static uint32_t read_u32_(const uint8_t *data);
  static float read_f32_(const uint8_t *data);
  static std::string format_hex_(const uint8_t *data, size_t length);
  static std::string format_object_id_(uint32_t object_id);
  static std::string format_request_id_(const RequestSpec &request);
  static std::string format_local_timestamp_(uint16_t year, uint8_t month, uint8_t day, uint8_t hour,
                                             uint8_t minute, uint8_t second, const uint8_t *dst,
                                             const uint8_t *utc_offset);
  static std::string format_clock_timestamp_(const uint8_t *clock, const uint8_t *dst, const uint8_t *utc_offset);
  static std::string format_dst_schedule_(const uint8_t *data, size_t length);
  static std::string format_utc_offset_(const uint8_t *data, size_t length);
  static std::string format_event_timestamp_(uint32_t timestamp, const uint8_t *dst, const uint8_t *utc_offset);
  static bool is_event_scan_request_(const RequestSpec &request);
  static bool event_id_is_newer_(uint16_t candidate, uint16_t current);
  static std::string manual_start_return_mode_(uint8_t value);
  static std::string control_selection_(uint16_t value);
  static bool due_(uint32_t now, uint32_t deadline) {
    return static_cast<int32_t>(now - deadline) >= 0;
  }

  std::array<sensor::Sensor *, BASE_SENSOR_COUNT> sensors_{};
  std::array<std::array<sensor::Sensor *, PUMP_SENSOR_COUNT>, 2> pump_sensors_{};
  std::array<binary_sensor::BinarySensor *, BINARY_SENSOR_COUNT> binary_sensors_{};
  std::array<text_sensor::TextSensor *, TEXT_SENSOR_COUNT> text_sensors_{};

  uint32_t fast_update_interval_{2000};
  uint32_t regular_update_interval_{30000};
  uint32_t slow_update_interval_{300000};
  uint32_t next_fast_update_{0};
  uint32_t next_regular_update_{0};
  uint32_t next_slow_update_{0};
  uint32_t next_request_at_{0};
  uint32_t request_deadline_{0};
  uint32_t notify_registered_at_{0};

  uint16_t characteristic_handle_{0};
#ifdef USE_ESP32
  esp_gatt_write_type_t write_type_{ESP_GATT_WRITE_TYPE_NO_RSP};
#endif
  bool notification_registered_{false};
  bool notification_registration_pending_{false};
  bool authenticated_{false};
  bool ready_{false};
  bool request_pending_{false};
  bool initial_event_scan_{true};
  uint16_t latest_event_id_{0};
  bool event_scan_failed_{false};
  bool have_scanned_latest_alarm_{false};
  bool have_scanned_latest_warning_{false};
  bool have_published_latest_alarm_{false};
  bool have_published_latest_warning_{false};
  bool have_controller_local_time_{false};
  bool have_last_clock_sync_{false};
  bool have_dst_schedule_{false};
  bool have_standard_utc_offset_{false};
  bool warned_zero_utc_offset_{false};
  EventRecord scanned_latest_alarm_{};
  EventRecord scanned_latest_warning_{};
  uint16_t published_latest_alarm_id_{0};
  uint16_t published_latest_warning_id_{0};
  uint32_t protocol_errors_{0};

  RequestSpec pending_request_{};
  std::array<uint8_t, 7> controller_local_time_data_{};
  std::array<uint8_t, 10> last_clock_sync_data_{};
  std::array<uint8_t, 10> dst_schedule_data_{};
  std::array<uint8_t, 2> standard_utc_offset_data_{};
  std::deque<RequestSpec> request_queue_;
  std::vector<uint8_t> receive_buffer_;
};

}  // namespace esphome::grundfos_lc231
