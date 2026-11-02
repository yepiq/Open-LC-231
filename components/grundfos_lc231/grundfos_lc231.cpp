#include "grundfos_lc231.h"

#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>

#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

namespace esphome::grundfos_lc231 {

static const char *const TAG = "grundfos_lc231";
namespace espbt = esphome::esp32_ble_tracker;

static bool is_leap_year(uint16_t year) {
  return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
}

static uint8_t days_in_month(uint16_t year, uint8_t month) {
  static const uint8_t DAYS[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if (month < 1 || month > 12)
    return 0;
  if (month == 2 && is_leap_year(year))
    return 29;
  return DAYS[month - 1];
}

static uint8_t iso_weekday(uint16_t year, uint8_t month, uint8_t day) {
  static const uint8_t MONTH_OFFSETS[] = {0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4};
  uint32_t adjusted_year = year;
  if (month < 3)
    adjusted_year--;
  const uint8_t sunday_based =
      (adjusted_year + adjusted_year / 4 - adjusted_year / 100 + adjusted_year / 400 + MONTH_OFFSETS[month - 1] +
       day) %
      7;
  return sunday_based == 0 ? 7 : sunday_based;
}

static uint8_t transition_day(uint16_t year, uint8_t month, uint8_t weekday, uint8_t week) {
  const uint8_t month_days = days_in_month(year, month);
  if (month_days == 0 || weekday < 1 || weekday > 7 || week < 1 || week > 5)
    return 0;

  if (week == 5) {
    const uint8_t last_weekday = iso_weekday(year, month, month_days);
    return month_days - (last_weekday + 7 - weekday) % 7;
  }

  const uint8_t first_weekday = iso_weekday(year, month, 1);
  const uint8_t day = 1 + (weekday + 7 - first_weekday) % 7 + (week - 1) * 7;
  return day <= month_days ? day : 0;
}

static const RequestSpec IDENTIFICATION_REQUESTS[] = {
    {RequestKind::STRING, 0x01, -1, 0, -1},
    {RequestKind::STRING, 0x08, -1, 0, -1},
    {RequestKind::STRING, 0x09, -1, 0, -1},
    {RequestKind::STRING, 0x0A, -1, 0, -1},
    {RequestKind::STRING, 0x11, -1, 0, -1},
    {RequestKind::STRING, 0x14, -1, 0, -1},
    {RequestKind::STRING, 0x32, -1, 0, -1},
    {RequestKind::STRING, 0x34, -1, 0, -1},
    {RequestKind::STRING, 0x3A, -1, 0, -1},
};

static const RequestSpec SESSION_SNAPSHOT_REQUESTS[] = {
    {RequestKind::OBJECT, 0x5A0000, 0x0000, 0xED05, 37},
    {RequestKind::OBJECT, 0x560001, 0x0000, 0xE301, 4},
    {RequestKind::OBJECT, 0x5B0001, 0x0001, 0x0301, 3},
    {RequestKind::OBJECT, 0x5B0002, 0x0001, 0x7C01, 9},
    {RequestKind::OBJECT, 0x5E0000, 0x0001, 0x0801, 2},
    {RequestKind::OBJECT, 0x5E0001, 0x0001, 0x0B01, 2},
    {RequestKind::OBJECT, 0x5E0002, 0x0001, 0x0D01, 3},
    {RequestKind::OBJECT, 0x5E0003, 0x0001, 0x0D01, 7},
    {RequestKind::OBJECT, 0x5E0004, 0x0001, 0x1701, 1},
    {RequestKind::OBJECT, 0x5E0064, 0x0001, 0x4101, 10},
    {RequestKind::OBJECT, 0x5E0066, 0x0001, 0x4301, 10},
    {RequestKind::OBJECT, 0x5E0067, 0x0002, 0x4D01, 2},
};

static const RequestSpec FAST_REQUESTS[] = {
    {RequestKind::OBJECT, 0x5A0003, 0x0001, 0x0901, 13},
    {RequestKind::OBJECT, 0x570045, 0x0001, 0x0002, 41},
    {RequestKind::OBJECT, 0x570047, 0x0002, 0x6801, 8},
    {RequestKind::OBJECT, 0x570085, 0x0001, 0x0002, 41},
    {RequestKind::OBJECT, 0x570087, 0x0002, 0x6801, 8},
};

static const RequestSpec REGULAR_REQUESTS[] = {
    {RequestKind::OBJECT, 0x560000, 0x0000, 0xE401, 6},
    {RequestKind::OBJECT, 0x520C00, 0x0000, 0xF502, 21},
    {RequestKind::OBJECT, 0x570001, 0x0000, 0xE801, 38},
    {RequestKind::OBJECT, 0x5D0000, 0x0000, 0xF701, 12},
    {RequestKind::OBJECT, 0x5D0001, 0x0000, 0xF802, 28},
    {RequestKind::OBJECT, 0x5D0002, 0x0000, 0xF802, 28},
    {RequestKind::OBJECT, 0x5D0051, 0x0001, 0x6001, 12},
    {RequestKind::OBJECT, 0x5D0052, 0x0001, 0x6001, 12},
    {RequestKind::OBJECT, 0x5D0093, 0x0001, 0x1403, 13},
    {RequestKind::OBJECT, 0x5D00D3, 0x0001, 0x1103, 12},
    {RequestKind::OBJECT, 0x5D00D4, 0x0001, 0x1103, 12},
    {RequestKind::OBJECT, 0x5E0065, 0x0001, 0x4201, 12},
};

static const RequestSpec SLOW_REQUESTS[] = {
    {RequestKind::OBJECT, 0x580000, -1, 0x3A01, -1},
    {RequestKind::OBJECT, 0x58000B, -1, 0x3A01, -1},
    {RequestKind::OBJECT, 0x5827D7, 0x0000, 0xF301, 7},
};

void GrundfosLC231Component::setup() {
  this->publish_binary_(CONNECTED, false);
  this->publish_sensor_(PROTOCOL_ERRORS, 0);
  this->publish_text_(PROTOCOL_STATUS, "Disconnected");
}

void GrundfosLC231Component::dump_config() {
  ESP_LOGCONFIG(TAG, "Grundfos LC-231:");
  ESP_LOGCONFIG(TAG, "  Fast update interval: %" PRIu32 " ms", this->fast_update_interval_);
  ESP_LOGCONFIG(TAG, "  Regular update interval: %" PRIu32 " ms", this->regular_update_interval_);
  ESP_LOGCONFIG(TAG, "  Slow update interval: %" PRIu32 " ms", this->slow_update_interval_);
  ESP_LOGCONFIG(TAG, "  Scope: read-only Class 0x07 string and Class 0x0A object requests");
}

void GrundfosLC231Component::loop() {
  const uint32_t now = millis();

  if (this->notification_registered_ && this->authenticated_ && !this->ready_ &&
      this->due_(now, this->notify_registered_at_ + 300)) {
    this->become_ready_();
  }

  if (this->request_pending_ && this->due_(now, this->request_deadline_)) {
    if (this->is_event_scan_request_(this->pending_request_))
      this->event_scan_failed_ = true;
    this->record_error_("Reply timeout for " + this->format_request_id_(this->pending_request_));
    this->request_pending_ = false;
    this->next_request_at_ = now + 500;
  }

  if (!this->ready_ || this->request_pending_)
    return;

  if (this->request_queue_.empty())
    this->schedule_requests_(now);

  if (!this->request_queue_.empty() && this->due_(now, this->next_request_at_))
    this->send_next_request_();
}

#ifdef USE_ESP32
void GrundfosLC231Component::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                                                 esp_ble_gattc_cb_param_t *param) {
  switch (event) {
    case ESP_GATTC_SEARCH_CMPL_EVT: {
      this->reset_connection_state_();
      const auto service_uuid = espbt::ESPBTUUID::from_uint16(0xFE5D);
      const uint8_t characteristic_uuid_raw[16] = {0xA9, 0x7B, 0xB8, 0x85, 0x00, 0x1A, 0x28, 0xAA,
                                                    0x2A, 0x43, 0x6E, 0x03, 0xD1, 0xFF, 0x9C, 0x85};
      const auto characteristic_uuid = espbt::ESPBTUUID::from_raw(characteristic_uuid_raw);
      auto *characteristic = this->parent()->get_characteristic(service_uuid, characteristic_uuid);
      if (characteristic == nullptr) {
        this->record_error_("Vendor characteristic was not discovered");
        this->node_state = espbt::ClientState::ESTABLISHED;
        break;
      }

      this->characteristic_handle_ = characteristic->handle;
      if ((characteristic->properties & ESP_GATT_CHAR_PROP_BIT_NOTIFY) == 0) {
        this->record_error_("Vendor characteristic does not support notifications");
        this->node_state = espbt::ClientState::ESTABLISHED;
        break;
      }
      if (characteristic->properties & ESP_GATT_CHAR_PROP_BIT_WRITE_NR) {
        this->write_type_ = ESP_GATT_WRITE_TYPE_NO_RSP;
      } else if (characteristic->properties & ESP_GATT_CHAR_PROP_BIT_WRITE) {
        this->write_type_ = ESP_GATT_WRITE_TYPE_RSP;
      } else {
        this->record_error_("Vendor characteristic is not writable");
        this->node_state = espbt::ClientState::ESTABLISHED;
        break;
      }

      this->publish_text_(PROTOCOL_STATUS, "Pairing");
      this->register_for_notifications_();
      break;
    }

    case ESP_GATTC_REG_FOR_NOTIFY_EVT:
      if (param->reg_for_notify.handle != this->characteristic_handle_ || !this->notification_registration_pending_)
        break;
      this->notification_registration_pending_ = false;
      if (param->reg_for_notify.status != ESP_GATT_OK) {
        this->record_error_("Notification registration failed");
        break;
      }
      this->notification_registered_ = true;
      this->notify_registered_at_ = millis();
      this->node_state = espbt::ClientState::ESTABLISHED;
      this->publish_text_(PROTOCOL_STATUS, this->authenticated_ ? "Subscribing" : "Pairing");
      break;

    case ESP_GATTC_NOTIFY_EVT:
      if (param->notify.handle == this->characteristic_handle_)
        this->feed_notification_(param->notify.value, param->notify.value_len);
      break;

    case ESP_GATTC_CLOSE_EVT:
    case ESP_GATTC_DISCONNECT_EVT:
      this->reset_connection_state_();
      break;

    default:
      break;
  }
}

void GrundfosLC231Component::gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
  if (event != ESP_GAP_BLE_AUTH_CMPL_EVT ||
      !this->parent()->check_addr(param->ble_security.auth_cmpl.bd_addr))
    return;

  if (!param->ble_security.auth_cmpl.success) {
    this->record_error_("BLE pairing/authentication failed");
    return;
  }

  this->authenticated_ = true;
  ESP_LOGI(TAG, "BLE pairing/authentication complete");
  if (!this->notification_registered_)
    this->register_for_notifications_();
}
#endif

void GrundfosLC231Component::reset_connection_state_() {
  this->characteristic_handle_ = 0;
  this->notification_registered_ = false;
  this->notification_registration_pending_ = false;
  this->authenticated_ = false;
  this->ready_ = false;
  this->request_pending_ = false;
  this->initial_event_scan_ = true;
  this->event_scan_failed_ = false;
  this->have_scanned_latest_alarm_ = false;
  this->have_scanned_latest_warning_ = false;
  this->have_controller_local_time_ = false;
  this->have_last_clock_sync_ = false;
  this->have_dst_schedule_ = false;
  this->have_standard_utc_offset_ = false;
  this->warned_zero_utc_offset_ = false;
  this->request_queue_.clear();
  this->receive_buffer_.clear();
  this->publish_binary_(CONNECTED, false);
  this->publish_text_(PROTOCOL_STATUS, "Disconnected");
}

void GrundfosLC231Component::register_for_notifications_() {
#ifdef USE_ESP32
  if (this->characteristic_handle_ == 0 || this->notification_registered_ || this->notification_registration_pending_)
    return;
  this->notification_registration_pending_ = true;
  const auto status = this->parent()->register_for_notify(this->characteristic_handle_);
  if (status != ESP_OK) {
    this->notification_registration_pending_ = false;
    this->record_error_("Could not register vendor notifications");
  }
#endif
}

void GrundfosLC231Component::become_ready_() {
  this->ready_ = true;
  this->status_clear_warning();
  this->publish_binary_(CONNECTED, true);
  this->publish_text_(PROTOCOL_STATUS, "Ready");
  const uint32_t now = millis();
  this->next_fast_update_ = now;
  this->next_regular_update_ = now;
  this->next_slow_update_ = now;
  this->next_request_at_ = now;
  this->enqueue_identification_();
  this->enqueue_session_snapshot_();
  ESP_LOGI(TAG, "Vendor notifications ready; starting read-only polling");
}

void GrundfosLC231Component::schedule_requests_(uint32_t now) {
  if (this->due_(now, this->next_fast_update_)) {
    this->enqueue_fast_();
    this->next_fast_update_ = now + this->fast_update_interval_;
  }
  if (this->due_(now, this->next_regular_update_)) {
    this->enqueue_regular_();
    this->next_regular_update_ = now + this->regular_update_interval_;
  }
  if (this->due_(now, this->next_slow_update_)) {
    this->enqueue_slow_();
    this->next_slow_update_ = now + this->slow_update_interval_;
  }
}

void GrundfosLC231Component::enqueue_identification_() {
  for (const auto &request : IDENTIFICATION_REQUESTS)
    this->request_queue_.push_back(request);
}

void GrundfosLC231Component::enqueue_session_snapshot_() {
  for (const auto &request : SESSION_SNAPSHOT_REQUESTS)
    this->request_queue_.push_back(request);
}

void GrundfosLC231Component::enqueue_fast_() {
  for (const auto &request : FAST_REQUESTS)
    this->request_queue_.push_back(request);
}

void GrundfosLC231Component::enqueue_regular_() {
  for (const auto &request : REGULAR_REQUESTS)
    this->request_queue_.push_back(request);
}

void GrundfosLC231Component::enqueue_slow_() {
  for (const auto &request : SLOW_REQUESTS)
    this->request_queue_.push_back(request);
}

void GrundfosLC231Component::enqueue_event_scan_() {
  this->event_scan_failed_ = false;
  this->have_scanned_latest_alarm_ = false;
  this->have_scanned_latest_warning_ = false;
  for (uint16_t sub_id = 0x27D8; sub_id <= 0x27FF; sub_id++)
    this->request_queue_.push_back(
        {RequestKind::OBJECT, static_cast<uint32_t>(0x580000 | sub_id), 0x0000, 0xF402, 13});
  this->request_queue_.push_back({RequestKind::EVENT_SCAN_COMPLETE, 0, 0, 0, 0});
}

bool GrundfosLC231Component::send_next_request_() {
#ifdef USE_ESP32
  if (this->request_queue_.empty() || this->characteristic_handle_ == 0)
    return false;

  this->pending_request_ = this->request_queue_.front();
  this->request_queue_.pop_front();

  if (this->pending_request_.kind == RequestKind::EVENT_SCAN_COMPLETE) {
    if (this->event_scan_failed_) {
      this->initial_event_scan_ = true;
      ESP_LOGW(TAG, "Event history scan incomplete; retrying on the next slow update");
    } else {
      this->publish_latest_events_();
    }
    this->next_request_at_ = millis() + 50;
    return true;
  }

  std::vector<uint8_t> request;
  if (this->pending_request_.kind == RequestKind::STRING) {
    request = {0x27, 0x05, 0xFF, 0xF8, 0x07, 0x01, static_cast<uint8_t>(this->pending_request_.id), 0, 0};
  } else {
    request = {
        0x27,
        0x07,
        0xE7,
        0xF8,
        0x0A,
        0x03,
        static_cast<uint8_t>((this->pending_request_.id >> 16) & 0xFF),
        static_cast<uint8_t>((this->pending_request_.id >> 8) & 0xFF),
        static_cast<uint8_t>(this->pending_request_.id & 0xFF),
        0,
        0,
    };
  }
  const uint16_t crc = this->crc16_ccitt_(&request[1], request.size() - 3);
  request[request.size() - 2] = static_cast<uint8_t>(crc >> 8);
  request[request.size() - 1] = static_cast<uint8_t>(crc & 0xFF);

  const auto status = esp_ble_gattc_write_char(
      this->parent()->get_gattc_if(), this->parent()->get_conn_id(), this->characteristic_handle_,
      static_cast<uint16_t>(request.size()), request.data(), this->write_type_, ESP_GATT_AUTH_REQ_NONE);
  if (status != ESP_OK) {
    if (this->is_event_scan_request_(this->pending_request_))
      this->event_scan_failed_ = true;
    this->record_error_("Write failed for " + this->format_request_id_(this->pending_request_));
    this->next_request_at_ = millis() + 250;
    return false;
  }

  ESP_LOGV(TAG, "Request %s", this->format_request_id_(this->pending_request_).c_str());
  this->request_pending_ = true;
  this->request_deadline_ = millis() + 2000;
  return true;
#else
  return false;
#endif
}

void GrundfosLC231Component::finish_request_() {
  this->request_pending_ = false;
  this->next_request_at_ = millis() + 50;
}

void GrundfosLC231Component::record_error_(const std::string &message) {
  this->protocol_errors_++;
  this->publish_sensor_(PROTOCOL_ERRORS, this->protocol_errors_);
  this->publish_text_(PROTOCOL_STATUS, message);
  this->status_set_warning();
  ESP_LOGW(TAG, "%s", message.c_str());
}

void GrundfosLC231Component::feed_notification_(const uint8_t *data, size_t length) {
  this->receive_buffer_.insert(this->receive_buffer_.end(), data, data + length);
  while (!this->receive_buffer_.empty()) {
    auto first = this->receive_buffer_.begin();
    while (first != this->receive_buffer_.end() && *first != 0x24 && *first != 0x26 && *first != 0x27)
      first++;
    if (first != this->receive_buffer_.begin())
      this->receive_buffer_.erase(this->receive_buffer_.begin(), first);
    if (this->receive_buffer_.size() < 2)
      return;

    const size_t frame_length = static_cast<size_t>(this->receive_buffer_[1]) + 4;
    if (frame_length > 260) {
      this->receive_buffer_.erase(this->receive_buffer_.begin());
      continue;
    }
    if (this->receive_buffer_.size() < frame_length)
      return;

    std::vector<uint8_t> frame(this->receive_buffer_.begin(), this->receive_buffer_.begin() + frame_length);
    this->receive_buffer_.erase(this->receive_buffer_.begin(), this->receive_buffer_.begin() + frame_length);
    this->handle_frame_(frame);
  }
}

void GrundfosLC231Component::handle_frame_(const std::vector<uint8_t> &frame) {
  if (!this->request_pending_) {
    ESP_LOGD(TAG, "Unassociated notification: %s", this->format_hex_(frame.data(), frame.size()).c_str());
    return;
  }

  const uint8_t *data = nullptr;
  size_t data_length = 0;
  if (!this->validate_reply_(frame, this->pending_request_, &data, &data_length)) {
    if (this->is_event_scan_request_(this->pending_request_))
      this->event_scan_failed_ = true;
    this->record_error_("Invalid reply for " + this->format_request_id_(this->pending_request_));
    this->finish_request_();
    return;
  }

  if (this->pending_request_.kind == RequestKind::STRING)
    this->decode_string_(static_cast<uint8_t>(this->pending_request_.id), data, data_length);
  else
    this->decode_object_(this->pending_request_.id, data, data_length);
  this->status_clear_warning();
  this->publish_text_(PROTOCOL_STATUS, "Ready");
  this->finish_request_();
}

bool GrundfosLC231Component::validate_reply_(const std::vector<uint8_t> &frame, const RequestSpec &request,
                                             const uint8_t **data, size_t *data_length) {
  if (frame.size() < 8 || frame[0] != 0x24 || frame[1] + 4 != frame.size() || frame[2] != 0xF8 ||
      frame[3] != 0xE7)
    return false;

  const uint16_t seen_crc = this->read_u16_(&frame[frame.size() - 2]);
  if (seen_crc != this->crc16_ccitt_(&frame[1], frame.size() - 3))
    return false;

  if (request.kind == RequestKind::STRING) {
    if (frame.size() < 9 || frame[4] != 0x07)
      return false;
    const size_t encoded_length = frame[5];
    if (encoded_length < 1 || frame.size() != encoded_length + 8 || frame[5 + encoded_length] != 0)
      return false;
    *data = &frame[6];
    *data_length = encoded_length - 1;
    return true;
  }

  if (frame.size() < 15)
    return false;
  if (frame[4] != 0x0A || frame[5] != frame.size() - 8)
    return false;
  if (frame[10] != 0 || frame[11] != 0)
    return false;

  const size_t length = frame[12];
  if (frame.size() != length + 15)
    return false;
  if (request.expected_meta >= 0 && this->read_u16_(&frame[6]) != request.expected_meta)
    return false;
  if (this->read_u16_(&frame[8]) != request.expected_type_version)
    return false;
  if (request.expected_data_length >= 0 && length != request.expected_data_length)
    return false;

  *data = &frame[13];
  *data_length = length;
  return true;
}

void GrundfosLC231Component::decode_string_(uint8_t string_id, const uint8_t *data, size_t length) {
  const std::string value(reinterpret_cast<const char *>(data), length);
  switch (string_id) {
    case 0x01:
      this->publish_text_(PRODUCT_TYPE, value);
      break;
    case 0x08:
      this->publish_text_(PRODUCT_NUMBER, value);
      break;
    case 0x09:
      this->publish_text_(SERIAL_NUMBER, value);
      break;
    case 0x0A:
      if (length == 4 && data[0] >= '0' && data[0] <= '9' && data[1] >= '0' && data[1] <= '9' &&
          data[2] >= '0' && data[2] <= '9' && data[3] >= '0' && data[3] <= '9') {
        this->publish_text_(PRODUCTION_CODE,
                            str_sprintf("Y%c%c W%c%c", data[0], data[1], data[2], data[3]));
      } else {
        this->publish_text_(PRODUCTION_CODE, value);
      }
      break;
    case 0x11:
      this->publish_text_(SYSTEM_NAME, value);
      break;
    case 0x14:
      this->publish_text_(GSC_VERSION, value);
      break;
    case 0x32:
      this->publish_text_(APPLICATION_SOFTWARE, value);
      break;
    case 0x34:
      this->publish_text_(MOTOR_CONTROL_SOFTWARE, value);
      break;
    case 0x3A:
      this->publish_text_(BLE_SOFTWARE, value);
      break;
    default:
      break;
  }
}

void GrundfosLC231Component::decode_object_(uint32_t object_id, const uint8_t *data, size_t length) {
  switch (object_id) {
    case 0x560000:
      if (length == 6) {
        this->publish_text_(PUMP_1_CONTROL_SELECTION, this->control_selection_(this->read_u16_(&data[2])));
        this->publish_text_(PUMP_2_CONTROL_SELECTION, this->control_selection_(this->read_u16_(&data[4])));
      }
      break;
    case 0x5A0003:
      if (length == 13) {
        this->publish_sensor_(WATER_LEVEL, this->read_f32_(&data[0]));
        if (data[10] <= 1)
          this->publish_binary_(ABOVE_STOP_LEVEL, data[10] == 1);
        this->publish_binary_(PUMP_1_RUNNING, (data[11] & 0x20) != 0);
        this->publish_binary_(PUMP_2_RUNNING, (data[11] & 0x40) != 0);
      }
      break;
    case 0x520C00:
      if (length == 21) {
        this->publish_sensor_(CIO1_LEVEL, this->read_f32_(&data[12]));
        this->publish_sensor_(CIO1_INPUT_CURRENT, this->read_f32_(&data[16]));
      }
      break;
    case 0x5D0000:
      if (length == 12) {
        this->publish_sensor_(CONTROLLER_TOTAL_ON_TIME, this->read_u32_(&data[4]) / 3600.0f);
        this->publish_sensor_(CONTROLLER_ACTIVE_PUMPING_TIME, this->read_u32_(&data[8]) / 3600.0f);
      }
      break;
    case 0x5D0001:
      this->decode_pump_counters_(0, data, length);
      break;
    case 0x5D0002:
      this->decode_pump_counters_(1, data, length);
      break;
    case 0x5D0051:
      this->decode_pump_daily_(0, data, length);
      break;
    case 0x5D0052:
      this->decode_pump_daily_(1, data, length);
      break;
    case 0x5D0093:
      if (length == 13) {
        this->publish_sensor_(MAINS_PHASE_TO_NEUTRAL_VOLTAGE, this->read_f32_(&data[0]));
        this->publish_sensor_(MAINS_PHASE_TO_PHASE_VOLTAGE, this->read_f32_(&data[4]));
        this->publish_sensor_(MAINS_UNIDENTIFIED_VOLTAGE, this->read_f32_(&data[8]));
      }
      break;
    case 0x570001:
      if (length == 38) {
        this->publish_sensor_(UNIDENTIFIED_TEMPERATURE, this->read_f32_(&data[0]));
        this->publish_sensor_(PUMP_1_TEMPERATURE, this->read_f32_(&data[18]));
        this->publish_sensor_(PUMP_2_TEMPERATURE, this->read_f32_(&data[30]));
      }
      break;
    case 0x5A0000:
      if (length == 37) {
        this->publish_text_(LEVEL_CONTROL_PREFIX_RAW, this->format_hex_(data, 9));
        this->publish_sensor_(HIGH_LEVEL_AFTER_RUN_TIME, this->read_u16_(&data[29]));
        this->publish_sensor_(STOP_DELAY_TIME, this->read_u16_(&data[31]));
      }
      break;
    case 0x560001:
      if (length == 4) {
        this->publish_sensor_(FORCED_START_TIME, this->read_u16_(&data[1]));
        this->publish_text_(MANUAL_START_RETURN_MODE, this->manual_start_return_mode_(data[3]));
      }
      break;
    case 0x5B0001:
      if (length == 3) {
        this->publish_sensor_(INTER_PUMP_DELAY, data[1]);
        this->publish_sensor_(MAXIMUM_RUNNING_PUMPS, data[2]);
      }
      break;
    case 0x5B0002:
      if (length == 9) {
        this->publish_binary_(RUN_AND_PAUSE_ENABLED, data[0] != 0);
        this->publish_sensor_(MAXIMUM_RUN_TIME, this->read_u32_(&data[1]));
        this->publish_sensor_(PUMP_PAUSE_TIME, this->read_u16_(&data[7]));
      }
      break;
    case 0x5E0000:
      this->publish_text_(CLOCK_RECORD_5E0000_RAW, this->format_hex_(data, length));
      break;
    case 0x5E0001:
      this->publish_text_(CLOCK_RECORD_5E0001_RAW, this->format_hex_(data, length));
      break;
    case 0x5E0002:
      this->publish_text_(CLOCK_RECORD_5E0002_RAW, this->format_hex_(data, length));
      break;
    case 0x5E0003:
      this->publish_text_(CLOCK_RECORD_5E0003_RAW, this->format_hex_(data, length));
      break;
    case 0x5E0004:
      this->publish_text_(CLOCK_RECORD_5E0004_RAW, this->format_hex_(data, length));
      break;
    case 0x5E0064:
      if (length == 10) {
        this->publish_binary_(AUTOMATIC_TIME_SETTING, data[0] != 0);
        std::memcpy(this->last_clock_sync_data_.data(), data, length);
        this->have_last_clock_sync_ = true;
        this->publish_last_clock_synchronization_();
      }
      break;
    case 0x5E0065:
      if (length == 12) {
        std::memcpy(this->controller_local_time_data_.data(), data, this->controller_local_time_data_.size());
        this->have_controller_local_time_ = true;
        this->publish_controller_local_time_();
        this->publish_sensor_(RTC_UNIDENTIFIED_BYTE_10, data[10]);
      }
      break;
    case 0x5E0066:
      if (length == 10) {
        std::memcpy(this->dst_schedule_data_.data(), data, length);
        this->have_dst_schedule_ = true;
        const auto value = this->format_dst_schedule_(data, length);
        if (!value.empty())
          this->publish_text_(DST_SCHEDULE, value);
        this->publish_controller_local_time_();
        this->publish_last_clock_synchronization_();
      }
      break;
    case 0x5E0067:
      if (length == 2) {
        std::memcpy(this->standard_utc_offset_data_.data(), data, length);
        this->have_standard_utc_offset_ = true;
        if (data[0] == 0 && data[1] == 0 && !this->warned_zero_utc_offset_) {
          ESP_LOGW(TAG,
                   "UTC offset record is zero; interpreting it as UTC+00:00, but LC-231 can also report zero for "
                   "negative UTC offsets");
          this->warned_zero_utc_offset_ = true;
        }
        const auto value = this->format_utc_offset_(data, length);
        if (!value.empty())
          this->publish_text_(STANDARD_UTC_OFFSET, value);
        this->publish_controller_local_time_();
        this->publish_last_clock_synchronization_();
      }
      break;
    case 0x570045:
      this->decode_live_electrical_(0, data, length);
      break;
    case 0x570085:
      this->decode_live_electrical_(1, data, length);
      break;
    case 0x570047:
      if (length == 8)
        this->publish_pump_sensor_(0, LIVE_POWER_FACTOR, this->read_f32_(&data[0]));
      break;
    case 0x570087:
      if (length == 8)
        this->publish_pump_sensor_(1, LIVE_POWER_FACTOR, this->read_f32_(&data[0]));
      break;
    case 0x5D00D3:
      this->decode_last_run_electrical_(0, data, length);
      break;
    case 0x5D00D4:
      this->decode_last_run_electrical_(1, data, length);
      break;
    case 0x580000:
      this->decode_active_conditions_(true, data, length);
      break;
    case 0x58000B:
      this->decode_active_conditions_(false, data, length);
      break;
    case 0x5827D7:
      if (length == 7) {
        const uint16_t latest = this->read_u16_(&data[0]);
        if (this->initial_event_scan_ || latest != this->latest_event_id_) {
          this->latest_event_id_ = latest;
          this->initial_event_scan_ = false;
          this->enqueue_event_scan_();
        }
      }
      break;
    default:
      if ((object_id & 0xFFFF00) == 0x582700)
        this->decode_event_(data, length);
      break;
  }
}

void GrundfosLC231Component::decode_active_conditions_(bool alarm, const uint8_t *data, size_t length) {
  bool active = false;
  for (size_t index = 0; index < length; index++)
    active |= data[index] != 0;

  this->publish_binary_(alarm ? ACTIVE_ALARM : ACTIVE_WARNING, active);
  this->publish_text_(alarm ? ACTIVE_ALARMS_RAW : ACTIVE_WARNINGS_RAW, this->format_hex_(data, length));
}

void GrundfosLC231Component::decode_pump_counters_(uint8_t pump, const uint8_t *data, size_t length) {
  if (length != 28)
    return;
  this->publish_pump_sensor_(pump, LIFETIME_RUNS, this->read_u32_(&data[0]));
  this->publish_pump_sensor_(pump, RUNS_LAST_HOUR, this->read_u16_(&data[4]));
  this->publish_pump_sensor_(pump, RUNS_LAST_24_HOURS, this->read_u16_(&data[6]));
  this->publish_pump_sensor_(pump, LIFETIME_RUNTIME, this->read_u32_(&data[8]) / 3600.0f);
  this->publish_pump_sensor_(pump, RUNTIME_LAST_24_HOURS, this->read_u32_(&data[12]) / 60.0f);
  this->publish_pump_sensor_(pump, LAST_RUN_DURATION, this->read_u32_(&data[16]) / 60.0f);
}

void GrundfosLC231Component::decode_pump_daily_(uint8_t pump, const uint8_t *data, size_t length) {
  if (length != 12)
    return;
  this->publish_pump_sensor_(pump, RUNS_TODAY, this->read_u16_(&data[0]));
  this->publish_pump_sensor_(pump, RUNS_YESTERDAY, this->read_u16_(&data[2]));
  this->publish_pump_sensor_(pump, RUNTIME_TODAY, this->read_u32_(&data[4]) / 60.0f);
  this->publish_pump_sensor_(pump, RUNTIME_YESTERDAY, this->read_u32_(&data[8]) / 60.0f);
}

void GrundfosLC231Component::decode_live_electrical_(uint8_t pump, const uint8_t *data, size_t length) {
  if (length != 41)
    return;
  this->publish_pump_sensor_(pump, LIVE_VOLTAGE, this->read_f32_(&data[0]));
  this->publish_pump_sensor_(pump, LIVE_CURRENT, this->read_f32_(&data[8]));
  this->publish_pump_sensor_(pump, LIVE_POWER, this->read_f32_(&data[12]));
}

void GrundfosLC231Component::decode_last_run_electrical_(uint8_t pump, const uint8_t *data, size_t length) {
  if (length != 12)
    return;
  this->publish_pump_sensor_(pump, LAST_RUN_CURRENT, this->read_f32_(&data[0]));
  this->publish_pump_sensor_(pump, LAST_RUN_POWER_FACTOR, this->read_f32_(&data[4]));
  this->publish_pump_sensor_(pump, LAST_RUN_POWER, this->read_f32_(&data[8]));
}

void GrundfosLC231Component::decode_event_(const uint8_t *data, size_t length) {
  if (length != 13)
    return;

  const EventRecord event{this->read_u16_(&data[0]), this->read_u16_(&data[2]), data[4], data[5], data[6],
                          this->read_u32_(&data[7]), this->read_u16_(&data[11])};

  const bool alarm = event.condition == 1 || event.condition == 2;
  const bool warning = event.condition == 3 || event.condition == 4;
  if (!alarm && !warning)
    return;

  bool &have_latest = alarm ? this->have_scanned_latest_alarm_ : this->have_scanned_latest_warning_;
  EventRecord &latest = alarm ? this->scanned_latest_alarm_ : this->scanned_latest_warning_;
  if (!have_latest || this->event_id_is_newer_(event.event_id, latest.event_id)) {
    have_latest = true;
    latest = event;
  }
}

void GrundfosLC231Component::publish_latest_events_() {
  if (this->have_scanned_latest_alarm_ &&
      (!this->have_published_latest_alarm_ ||
       this->scanned_latest_alarm_.event_id != this->published_latest_alarm_id_)) {
    this->publish_event_(this->scanned_latest_alarm_);
    this->published_latest_alarm_id_ = this->scanned_latest_alarm_.event_id;
    this->have_published_latest_alarm_ = true;
  }

  if (this->have_scanned_latest_warning_ &&
      (!this->have_published_latest_warning_ ||
       this->scanned_latest_warning_.event_id != this->published_latest_warning_id_)) {
    this->publish_event_(this->scanned_latest_warning_);
    this->published_latest_warning_id_ = this->scanned_latest_warning_.event_id;
    this->have_published_latest_warning_ = true;
  }
}

void GrundfosLC231Component::publish_event_(const EventRecord &event) {
  const bool alarm = event.condition == 1 || event.condition == 2;

  std::string value;
  if (event.code == 9)
    value = "Phase sequence reversal";
  else if (event.code == 25)
    value = "Setup conflict";
  else {
    char code_text[24];
    std::snprintf(code_text, sizeof(code_text), "Event code %u", event.code);
    value = code_text;
  }

  if (event.source == 0) {
    value += " | Controller";
  } else if (event.source == 6 && (event.device == 1 || event.device == 2)) {
    value += event.device == 1 ? " | Pump 1" : " | Pump 2";
  } else if (event.source == 6) {
    value += " | Pump";
  }

  std::string event_time;
  if (this->have_dst_schedule_ && this->have_standard_utc_offset_) {
    event_time = this->format_event_timestamp_(event.timestamp, this->dst_schedule_data_.data(),
                                                this->standard_utc_offset_data_.data());
    if (event_time.size() >= 19) {
      std::string display_time = event_time.substr(0, 19);
      display_time[10] = ' ';
      value += " | " + display_time;
    }
  }

  if (event.code == 25 && event.detail == 1005) {
    value += " | Level control settings wrong";
  } else if (event.detail != 0) {
    char detail_text[24];
    std::snprintf(detail_text, sizeof(detail_text), " | Detail %u", event.detail);
    value += detail_text;
  }

  this->publish_text_(alarm ? LAST_ALARM : LAST_WARNING, value);
  if (!event_time.empty())
    this->publish_text_(alarm ? LAST_ALARM_TIME : LAST_WARNING_TIME, event_time);
}

void GrundfosLC231Component::publish_controller_local_time_() {
  if (!this->have_controller_local_time_ || !this->have_dst_schedule_ || !this->have_standard_utc_offset_)
    return;

  const auto *clock = this->controller_local_time_data_.data();
  const auto value = this->format_local_timestamp_(this->read_u16_(clock), clock[2], clock[3], clock[4], clock[5],
                                                    clock[6], this->dst_schedule_data_.data(),
                                                    this->standard_utc_offset_data_.data());
  if (!value.empty())
    this->publish_text_(CONTROLLER_LOCAL_TIME, value);
}

void GrundfosLC231Component::publish_last_clock_synchronization_() {
  if (!this->have_last_clock_sync_ || !this->have_dst_schedule_ || !this->have_standard_utc_offset_)
    return;

  const auto value = this->format_clock_timestamp_(this->last_clock_sync_data_.data(), this->dst_schedule_data_.data(),
                                                    this->standard_utc_offset_data_.data());
  if (!value.empty())
    this->publish_text_(LAST_CLOCK_SYNCHRONIZATION, value);
}

void GrundfosLC231Component::publish_sensor_(BaseSensor index, float value) {
  if (this->sensors_[index] != nullptr && std::isfinite(value))
    this->sensors_[index]->publish_state(value);
}

void GrundfosLC231Component::publish_pump_sensor_(uint8_t pump, PumpSensor index, float value) {
  if (pump < 2 && this->pump_sensors_[pump][index] != nullptr && std::isfinite(value))
    this->pump_sensors_[pump][index]->publish_state(value);
}

void GrundfosLC231Component::publish_binary_(BinarySensorIndex index, bool value) {
  if (this->binary_sensors_[index] != nullptr)
    this->binary_sensors_[index]->publish_state(value);
}

void GrundfosLC231Component::publish_text_(TextSensorIndex index, const std::string &value) {
  auto *entity = this->text_sensors_[index];
  if (entity != nullptr && (!entity->has_state() || entity->state != value))
    entity->publish_state(value);
}

uint16_t GrundfosLC231Component::crc16_ccitt_(const uint8_t *data, size_t length) {
  uint16_t value = 0xFFFF;
  for (size_t index = 0; index < length; index++) {
    value ^= static_cast<uint16_t>(data[index]) << 8;
    for (uint8_t bit = 0; bit < 8; bit++)
      value = (value & 0x8000) ? static_cast<uint16_t>((value << 1) ^ 0x1021) : static_cast<uint16_t>(value << 1);
  }
  return value ^ 0xFFFF;
}

uint16_t GrundfosLC231Component::read_u16_(const uint8_t *data) {
  return (static_cast<uint16_t>(data[0]) << 8) | data[1];
}

uint32_t GrundfosLC231Component::read_u32_(const uint8_t *data) {
  return (static_cast<uint32_t>(data[0]) << 24) | (static_cast<uint32_t>(data[1]) << 16) |
         (static_cast<uint32_t>(data[2]) << 8) | data[3];
}

float GrundfosLC231Component::read_f32_(const uint8_t *data) {
  const uint32_t bits = read_u32_(data);
  float value;
  std::memcpy(&value, &bits, sizeof(value));
  return value;
}

std::string GrundfosLC231Component::format_hex_(const uint8_t *data, size_t length) {
  static const char HEX[] = "0123456789abcdef";
  std::string value;
  value.reserve(length * 2);
  for (size_t index = 0; index < length; index++) {
    value.push_back(HEX[data[index] >> 4]);
    value.push_back(HEX[data[index] & 0x0F]);
  }
  return value;
}

std::string GrundfosLC231Component::format_object_id_(uint32_t object_id) {
  char value[7];
  std::snprintf(value, sizeof(value), "%06x", static_cast<unsigned int>(object_id & 0xFFFFFF));
  return value;
}

std::string GrundfosLC231Component::format_request_id_(const RequestSpec &request) {
  if (request.kind == RequestKind::OBJECT)
    return "object " + format_object_id_(request.id);

  if (request.kind == RequestKind::EVENT_SCAN_COMPLETE)
    return "event scan completion";

  char value[12];
  std::snprintf(value, sizeof(value), "string 0x%02x", static_cast<unsigned int>(request.id));
  return value;
}

std::string GrundfosLC231Component::format_local_timestamp_(uint16_t year, uint8_t month, uint8_t day,
                                                            uint8_t hour, uint8_t minute, uint8_t second,
                                                            const uint8_t *dst, const uint8_t *utc_offset) {
  const uint8_t month_days = days_in_month(year, month);
  if (month_days == 0 || day < 1 || day > month_days || hour > 23 || minute > 59 || second > 59 ||
      utc_offset[1] > 59)
    return {};

  const int standard_hours = static_cast<int8_t>(utc_offset[0]);
  int offset_minutes = standard_hours * 60;
  offset_minutes += standard_hours < 0 ? -utc_offset[1] : utc_offset[1];

  if (dst[0] != 0) {
    const uint8_t start_day = transition_day(year, dst[1], dst[2], dst[3]);
    const uint8_t end_day = transition_day(year, dst[5], dst[6], dst[7]);
    if (start_day == 0 || end_day == 0 || dst[4] > 23 || dst[8] > 23)
      return {};

    const uint32_t current_key = month * 1000000UL + day * 10000UL + hour * 100UL + minute;
    const uint32_t start_key = dst[1] * 1000000UL + start_day * 10000UL + dst[4] * 100UL;
    const uint32_t end_key = dst[5] * 1000000UL + end_day * 10000UL + dst[8] * 100UL;
    const bool daylight_saving =
        start_key < end_key ? current_key >= start_key && current_key < end_key
                            : current_key >= start_key || current_key < end_key;
    if (daylight_saving)
      offset_minutes += dst[9];
  }

  if (offset_minutes < -1439 || offset_minutes > 1439)
    return {};
  const char sign = offset_minutes < 0 ? '-' : '+';
  const unsigned absolute_offset = static_cast<unsigned>(offset_minutes < 0 ? -offset_minutes : offset_minutes);

  char value[32];
  std::snprintf(value, sizeof(value), "%04u-%02u-%02uT%02u:%02u:%02u%c%02u:%02u", static_cast<unsigned>(year),
                static_cast<unsigned>(month), static_cast<unsigned>(day), static_cast<unsigned>(hour),
                static_cast<unsigned>(minute), static_cast<unsigned>(second), sign, absolute_offset / 60,
                absolute_offset % 60);
  return value;
}

std::string GrundfosLC231Component::format_clock_timestamp_(const uint8_t *clock, const uint8_t *dst,
                                                            const uint8_t *utc_offset) {
  return format_local_timestamp_(read_u16_(&clock[1]), clock[3], clock[4], clock[5], clock[6], clock[7], dst,
                                 utc_offset);
}

std::string GrundfosLC231Component::format_dst_schedule_(const uint8_t *data, size_t length) {
  if (length != 10)
    return {};

  static const char *const MONTH_NAMES[] = {"January", "February", "March", "April", "May", "June",
                                            "July", "August", "September", "October", "November", "December"};
  static const char *const WEEKDAY_NAMES[] = {"Monday", "Tuesday", "Wednesday", "Thursday",
                                              "Friday", "Saturday", "Sunday"};
  static const char *const WEEK_NAMES[] = {"first", "second", "third", "fourth", "last"};

  const bool fields_valid = data[1] >= 1 && data[1] <= 12 && data[2] >= 1 && data[2] <= 7 &&
                            data[3] >= 1 && data[3] <= 5 && data[4] <= 23 && data[5] >= 1 &&
                            data[5] <= 12 && data[6] >= 1 && data[6] <= 7 && data[7] >= 1 &&
                            data[7] <= 5 && data[8] <= 23;

  char value[192];
  if (fields_valid) {
    std::snprintf(value, sizeof(value), "%s | %s %s in %s at %02u:00 | %s %s in %s at %02u:00 | Shift: %u min",
                  data[0] != 0 ? "Enabled" : "Disabled", WEEK_NAMES[data[3] - 1], WEEKDAY_NAMES[data[2] - 1],
                  MONTH_NAMES[data[1] - 1], static_cast<unsigned>(data[4]), WEEK_NAMES[data[7] - 1],
                  WEEKDAY_NAMES[data[6] - 1], MONTH_NAMES[data[5] - 1], static_cast<unsigned>(data[8]),
                  static_cast<unsigned>(data[9]));
  } else {
    std::snprintf(value, sizeof(value),
                  "Enabled: %u | Start: month %u, weekday %u, week %u, hour %u | "
                  "End: month %u, weekday %u, week %u, hour %u | Shift: %u min",
                  static_cast<unsigned>(data[0]), static_cast<unsigned>(data[1]), static_cast<unsigned>(data[2]),
                  static_cast<unsigned>(data[3]), static_cast<unsigned>(data[4]), static_cast<unsigned>(data[5]),
                  static_cast<unsigned>(data[6]), static_cast<unsigned>(data[7]), static_cast<unsigned>(data[8]),
                  static_cast<unsigned>(data[9]));
  }
  return value;
}

std::string GrundfosLC231Component::format_utc_offset_(const uint8_t *data, size_t length) {
  if (length != 2)
    return {};
  if (data[1] > 59)
    return "Unrecognized";

  const int hours = static_cast<int8_t>(data[0]);
  const unsigned absolute_hours = static_cast<unsigned>(hours < 0 ? -hours : hours);
  char value[16];
  std::snprintf(value, sizeof(value), "UTC%c%02u:%02u", hours < 0 ? '-' : '+', absolute_hours,
                static_cast<unsigned>(data[1]));
  return value;
}

std::string GrundfosLC231Component::format_event_timestamp_(uint32_t timestamp, const uint8_t *dst,
                                                            const uint8_t *utc_offset) {
  if (timestamp == 0)
    return {};

  const std::time_t epoch = timestamp;
  // The controller encodes its local wall clock as a Unix-like scalar. Split
  // it without applying the receiver's timezone offset a second time.
  std::tm controller_time{};
  if (gmtime_r(&epoch, &controller_time) == nullptr)
    return {};

  return format_local_timestamp_(static_cast<uint16_t>(controller_time.tm_year + 1900),
                                 static_cast<uint8_t>(controller_time.tm_mon + 1),
                                 static_cast<uint8_t>(controller_time.tm_mday),
                                 static_cast<uint8_t>(controller_time.tm_hour),
                                 static_cast<uint8_t>(controller_time.tm_min),
                                 static_cast<uint8_t>(controller_time.tm_sec), dst, utc_offset);
}

bool GrundfosLC231Component::event_id_is_newer_(uint16_t candidate, uint16_t current) {
  return static_cast<int16_t>(candidate - current) > 0;
}

bool GrundfosLC231Component::is_event_scan_request_(const RequestSpec &request) {
  return request.kind == RequestKind::OBJECT && request.id >= 0x5827D8 && request.id <= 0x5827FF;
}

std::string GrundfosLC231Component::manual_start_return_mode_(uint8_t value) {
  switch (value) {
    case 0:
      return "Auto";
    case 1:
      return "Off";
    default: {
      char text[5];
      std::snprintf(text, sizeof(text), "0x%02x", value);
      return text;
    }
  }
}

std::string GrundfosLC231Component::control_selection_(uint16_t value) {
  switch (value) {
    case 0x0000:
      return "Auto";
    case 0x0201:
      return "Off";
    case 0x0205:
      return "On";
    default: {
      char text[7];
      std::snprintf(text, sizeof(text), "0x%04x", value);
      return text;
    }
  }
}

}  // namespace esphome::grundfos_lc231
